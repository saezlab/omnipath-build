import { Badge } from "@/components/ui/badge";
import { 
  Card, 
  CardContent, 
  CardFooter, 
  CardHeader, 
  CardTitle 
} from "@/components/ui/card";
import { Avatar, AvatarImage } from "@/components/ui/avatar";
import { Database, Network, Tag } from "lucide-react";
import Link from "next/link";
import { DataSource, CATEGORIES, ENTITY_TYPES } from "../types/datasource";

interface DatasourceCardProps {
  datasource: DataSource;
}

export function DatasourceCard({ datasource }: DatasourceCardProps) {
  // Get unique entity types and categories from all datasets
  const entityTypes = [...new Set(datasource.datasets.map(ds => ds.entityType))];
  const categories = [...new Set(datasource.datasets.map(ds => ds.category))];
  
  // Remove unused variables
  
  // Get category icon
  const getCategoryIcon = (category: string) => {
    switch (category) {
      case 'interaction':
        return <Network className="h-4 w-4" />;
      case 'annotation':
        return <Tag className="h-4 w-4" />;
      case 'ontology':
        return <Database className="h-4 w-4" />;
      default:
        return <Database className="h-4 w-4" />;
    }
  };

  return (
    <Link href={`/sources/${datasource.id}`}>
      <Card className="h-full hover:shadow-md transition-shadow cursor-pointer flex flex-col pt-3 pb-0">
        <CardHeader className="space-y-0 px-4 pb-2 pt-2">
          <div className="flex items-center gap-4">
            <Avatar className="h-8 w-8">
              <AvatarImage
                src={`https://avatar.vercel.sh/${datasource.name}`}
                alt={datasource.name}
              />
            </Avatar>
            <div className="flex flex-col flex-1 min-w-0">
              <CardTitle className="text-xl line-clamp-1">
                {datasource.name}
              </CardTitle>
            </div>
          </div>
        </CardHeader>

        <CardContent className="flex-1 space-y-3 border-t pt-2">
          <p className="text-sm text-muted-foreground line-clamp-5">
            {datasource.description || "No description available"}
          </p>
            
        </CardContent>
        <CardFooter className="px-2 border-t w-full [.border-t]:pt-0">
          <div className="flex items-center gap-3 overflow-x-auto h-12">
              {entityTypes.slice(0, 3).map(type => {
                const entityInfo = ENTITY_TYPES.find(e => e.value === type);
                return (
                  <Badge key={type} variant="secondary">
                    {entityInfo?.icon} {entityInfo?.label || type}
                  </Badge>
                );
              })}
              {entityTypes.length > 3 && (
                <Badge variant="secondary">
                  +{entityTypes.length - 3} more
                </Badge>
              )}
                            {categories.map(cat => {
                const categoryInfo = CATEGORIES.find(c => c.value === cat);
                return (
                  <Badge key={cat} variant="outline" className="item justify-between">
                    {getCategoryIcon(cat)}
                    <span>{categoryInfo?.label || cat}</span>
                  </Badge>
                );
              })}

            </div>

        </CardFooter>
      </Card>
    </Link>
  );
}