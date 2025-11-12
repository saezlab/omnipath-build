	entity {
		bigint id PK ""
        bigint entity_type FK ""  
	}
	entity_identifier {
		bigint id PK ""  
		bigint entity_id FK ""  
		varchar type_id FK ""  
		varchar identifier  ""  
	}
	entity_annotation {
		bigint id PK ""  
		bigint entity_id FK ""  
		int annotation_id FK ""  
		text annotation_value ""  
		int annotation_unit FK ""  
		int source_id FK ""  
	}
	source {
		int id PK ""  
		varchar name  ""  
		varchar url  ""  
		text description  ""  
	}
	membership {
		bigint id PK ""  
		bigint parent_id FK ""  
		bigint member_id FK ""  
		int source_id FK ""  
	}
	membership_annotation {
		bigint id PK ""  
		bigint membership_id FK ""  
		int annotation_id FK ""  
		text annotation_value ""  
		int annotation_unit FK ""  
		int source_id FK ""  
	}
	entity_identifier_source {
		int id PK ""  
		bigint entity_identifier_id FK ""  
		int source_id FK ""  
	}

	entity||--o{entity_identifier:"has identifiers"
	entity||--o{entity_annotation:"annotated by"
	entity||--o{membership:"as parent"
	entity||--o{membership:"as member"
	membership||--o{membership_annotation:"annotated by"
	source||--o{membership:"from"
	source||--o{membership_annotation:"from"
	source||--o{entity_annotation:"from"
	source||--o{entity_identifier_source:"from"
	entity_identifier||--o{entity_identifier_source:"has_source"
