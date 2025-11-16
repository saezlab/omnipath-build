"use client";

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Edit, Trash, Play } from 'lucide-react';
import {
  getTransformationFunctions,
  createTransformationFunction,
  updateTransformationFunction,
  deleteTransformationFunction,
  testTransformationFunction
} from '../api/function-mutations';

// Categories for functions
const CATEGORIES = [
  { value: 'string', label: 'String Manipulation' },
  { value: 'extraction', label: 'Pattern Extraction' },
  { value: 'normalization', label: 'Normalization' },
  { value: 'utility', label: 'Utilities' }
];

// Function template
const FUNCTION_TEMPLATE = `CREATE OR REPLACE FUNCTION function_name(field TEXT) 
RETURNS TEXT AS $$
BEGIN
    -- Your transformation logic here
    RETURN field;
END;
$$ LANGUAGE plpgsql IMMUTABLE;`;

export function FunctionManagement() {
  const [functions, setFunctions] = useState<Array<{ id: number; name: string; description?: string; category?: string; sqlDefinition: string; createdAt: string }>>([]);
  const [, setLoading] = useState(false);
  const [editingFunction, setEditingFunction] = useState<{ id: number; name: string; description?: string; category?: string; sqlDefinition: string; createdAt: string } | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  
  // Form state
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    category: 'utility',
    sqlDefinition: FUNCTION_TEMPLATE
  });
  
  // Test state
  const [testInput, setTestInput] = useState('');
  const [testOutput, setTestOutput] = useState('');
  
  // Load functions
  useEffect(() => {
    loadFunctions();
  }, []);
  
  async function loadFunctions() {
    try {
      const data = await getTransformationFunctions();
      setFunctions(data.filter(f => f.id !== null && f.name !== null && f.sqlDefinition !== null && f.createdAt !== null).map(f => ({
        id: f.id!,
        name: f.name!,
        description: f.description || undefined,
        category: f.category || undefined,
        sqlDefinition: f.sqlDefinition!,
        createdAt: f.createdAt!
      })));
    } catch {
      toast.error('Failed to load functions');
    } finally {
      setLoading(false);
    }
  }
  
  async function handleSave() {
    try {
      if (editingFunction) {
        await updateTransformationFunction(editingFunction.id, formData);
        toast.success('Function updated successfully');
      } else {
        await createTransformationFunction(formData);
        toast.success('Function created successfully');
      }
      setShowEditor(false);
      setEditingFunction(null);
      resetForm();
      loadFunctions();
    } catch (error) {
      toast.error((error as Error).message || 'Failed to save function');
    }
  }
  
  async function handleDelete(id: number) {
    if (!confirm('Are you sure you want to delete this function?')) return;
    
    try {
      await deleteTransformationFunction(id);
      toast.success('Function deleted successfully');
      loadFunctions();
    } catch {
      toast.error('Failed to delete function');
    }
  }
  
  async function handleTest() {
    try {
      const result = await testTransformationFunction(
        formData.sqlDefinition,
        testInput
      );
      
      if (result.success) {
        setTestOutput(String(result.output || 'null'));
      } else {
        setTestOutput(`Error: ${result.error}`);
      }
    } catch (error) {
      setTestOutput(`Error: ${(error as Error).message}`);
    }
  }
  
  function resetForm() {
    setFormData({
      name: '',
      description: '',
      category: 'utility',
      sqlDefinition: FUNCTION_TEMPLATE
    });
    setTestInput('');
    setTestOutput('');
  }
  
  return (
    <div className="container mx-auto p-6 max-w-6xl">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Transformation Functions</CardTitle>
            <Button onClick={() => {
              resetForm();
              setEditingFunction(null);
              setShowEditor(true);
            }}>
              <Plus className="w-4 h-4 mr-2" />
              New Function
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {/* Functions Table */}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {functions.map((func) => (
                <TableRow key={func.id}>
                  <TableCell className="font-mono">{func.name}</TableCell>
                  <TableCell>{func.description}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{func.category}</Badge>
                  </TableCell>
                  <TableCell>{new Date(func.createdAt).toLocaleDateString()}</TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditingFunction(func);
                          setFormData({
                            name: func.name,
                            description: func.description || '',
                            category: func.category || 'utility',
                            sqlDefinition: func.sqlDefinition
                          });
                          setShowEditor(true);
                        }}
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDelete(func.id)}
                      >
                        <Trash className="w-4 h-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      
      {/* Function Editor Dialog */}
      <Dialog open={showEditor} onOpenChange={setShowEditor}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editingFunction ? 'Edit Function' : 'Create Function'}
            </DialogTitle>
          </DialogHeader>
          
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="name">Function Name</Label>
                <Input
                  id="name"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="extract_custom_id"
                  disabled={!!editingFunction}
                />
              </div>
              <div>
                <Label htmlFor="category">Category</Label>
                <Select
                  value={formData.category}
                  onValueChange={(v) => setFormData({ ...formData, category: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((cat) => (
                      <SelectItem key={cat.value} value={cat.value}>
                        {cat.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            
            <div>
              <Label htmlFor="description">Description</Label>
              <Input
                id="description"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Extract custom identifier from formatted string"
              />
            </div>
            
            <div>
              <Label htmlFor="sql">SQL Definition</Label>
              <Textarea
                id="sql"
                value={formData.sqlDefinition}
                onChange={(e) => setFormData({ ...formData, sqlDefinition: e.target.value })}
                rows={15}
                className="font-mono text-sm"
              />
            </div>
            
            {/* Test Section */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Test Function</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="testInput">Test Input</Label>
                    <Input
                      id="testInput"
                      value={testInput}
                      onChange={(e) => setTestInput(e.target.value)}
                      placeholder="Enter test value"
                    />
                  </div>
                  <Button onClick={handleTest} variant="outline">
                    <Play className="w-4 h-4 mr-2" />
                    Run Test
                  </Button>
                  {testOutput && (
                    <div>
                      <Label>Output</Label>
                      <div className="p-3 bg-muted rounded-md font-mono text-sm">
                        {testOutput}
                      </div>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
            
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowEditor(false)}>
                Cancel
              </Button>
              <Button onClick={handleSave}>
                {editingFunction ? 'Update' : 'Create'} Function
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}