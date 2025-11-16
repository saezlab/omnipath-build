use std::fs::File;
use std::mem;
use std::path::PathBuf;

use parquet::file::reader::SerializedFileReader;
use parquet::record::{reader::RowIter, Row};
use serde_json::{to_writer, Map, Value};

use crate::byte_count::ByteCount;

pub struct ParquetChunker {
    row_iter: RowIter<'static>,
    buffer: Vec<u8>,
    size: usize,
    records_in_buffer: usize,
}

impl ParquetChunker {
    pub fn new(path: PathBuf, size: usize) -> Self {
        let file = File::open(path).unwrap();
        let reader = SerializedFileReader::new(file).unwrap();
        let row_iter = reader.into_iter();
        Self { row_iter, buffer: Vec::new(), size, records_in_buffer: 0 }
    }

    fn row_to_json(&self, row: &Row) -> Map<String, Value> {
        let mut map = Map::new();

        for (name, field) in row.get_column_iter() {
            let value = field_to_json_value(field);
            map.insert(name.to_string(), value);
        }

        map
    }
    fn write_object(&mut self, object: &Map<String, Value>) {
        if self.records_in_buffer == 0 {
            self.buffer.push(b'[');
        } else {
            self.buffer.push(b',');
        }
        to_writer(&mut self.buffer, object).unwrap();
        self.records_in_buffer += 1;
    }

    fn finalize_chunk(&mut self) -> Option<Vec<u8>> {
        if self.records_in_buffer == 0 {
            None
        } else {
            let mut chunk = mem::take(&mut self.buffer);
            chunk.push(b']');
            self.records_in_buffer = 0;
            Some(chunk)
        }
    }
}

fn field_to_json_value(field: &parquet::record::Field) -> Value {
    use parquet::record::Field;

    match field {
        Field::Null => Value::Null,
        Field::Bool(b) => Value::Bool(*b),
        Field::Byte(b) => Value::Number((*b as i64).into()),
        Field::Short(s) => Value::Number((*s as i64).into()),
        Field::Int(i) => Value::Number((*i as i64).into()),
        Field::Long(l) => Value::Number((*l).into()),
        Field::UByte(b) => Value::Number((*b as i64).into()),
        Field::UShort(s) => Value::Number((*s as i64).into()),
        Field::UInt(i) => Value::Number((*i as i64).into()),
        Field::ULong(l) => Value::Number((*l as i64).into()),
        Field::Float(f) => {
            if let Some(n) = serde_json::Number::from_f64(*f as f64) {
                Value::Number(n)
            } else {
                Value::Null
            }
        }
        Field::Float16(f) => {
            if let Some(n) = serde_json::Number::from_f64(f.to_f64()) {
                Value::Number(n)
            } else {
                Value::Null
            }
        }
        Field::Double(d) => {
            if let Some(n) = serde_json::Number::from_f64(*d) {
                Value::Number(n)
            } else {
                Value::Null
            }
        }
        Field::Decimal(d) => Value::String(format!("{:?}", d)),
        Field::Str(s) => Value::String(s.clone()),
        Field::Bytes(b) => {
            Value::String(base64::Engine::encode(&base64::engine::general_purpose::STANDARD, b))
        }
        Field::Date(d) => Value::Number((*d as i64).into()),
        Field::TimestampMillis(ts) => Value::Number((*ts).into()),
        Field::TimestampMicros(ts) => Value::Number((*ts).into()),
        Field::TimeMillis(t) => Value::Number((*t as i64).into()),
        Field::TimeMicros(t) => Value::Number((*t).into()),
        Field::Group(row) => {
            let mut map = Map::new();
            for (name, field) in row.get_column_iter() {
                map.insert(name.to_string(), field_to_json_value(field));
            }
            Value::Object(map)
        }
        Field::ListInternal(list) => {
            let elements = list.elements();
            let values: Vec<Value> = elements.iter().map(field_to_json_value).collect();
            Value::Array(values)
        }
        Field::MapInternal(map_field) => {
            let mut map = Map::new();
            for (key, value) in map_field.entries() {
                let key_str = match key {
                    Field::Str(s) => s.clone(),
                    _ => key.to_string(),
                };
                map.insert(key_str, field_to_json_value(value));
            }
            Value::Object(map)
        }
    }
}

impl Iterator for ParquetChunker {
    type Item = Vec<u8>;

    fn next(&mut self) -> Option<Self::Item> {
        while let Some(row_result) = self.row_iter.next() {
            let row = row_result.unwrap();
            let object = self.row_to_json(&row);

            // Evaluate the size it will take if we serialize it in the buffer
            let mut counter = ByteCount::new();
            to_writer(&mut counter, &object).unwrap();
            let delimiter_size = 1; // '[' for first record or ',' otherwise
            let closing_bracket_size = 1;
            let projected_len =
                self.buffer.len() + delimiter_size + counter.count() + closing_bracket_size;

            if self.records_in_buffer > 0 && projected_len > self.size {
                let chunk = self.finalize_chunk().unwrap();
                self.write_object(&object);
                return Some(chunk);
            } else {
                self.write_object(&object);
            }
        }

        // Return any remaining data
        self.finalize_chunk()
    }
}
