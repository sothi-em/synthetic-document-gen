import { Database, DatabaseZap, Search } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

export function LabelsPanel() {
  return (
    <div className="grid items-start gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database />
            Data labels
          </CardTitle>
          <CardDescription>
            Label generation, ChromaDB embedding, and semantic search will live
            here (requires the <code>embed</code> extra).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <div className="flex size-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
              <DatabaseZap className="size-6" />
            </div>
            <p className="font-medium">Coming soon</p>
            <p className="max-w-md text-sm text-muted-foreground">
              Once wired up, this panel will let you generate data labels for
              your companies, embed them into ChromaDB, and run semantic
              searches over the dataset.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pipeline</CardTitle>
          <CardDescription>Planned capabilities.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-3 text-sm">
            <li className="flex items-center gap-2.5">
              <Badge variant="secondary" className="shrink-0 bg-primary/10 text-primary">
                1
              </Badge>
              Generate labels from company profiles
            </li>
            <li className="flex items-center gap-2.5">
              <Badge variant="secondary" className="shrink-0 bg-primary/10 text-primary">
                2
              </Badge>
              Embed labels into ChromaDB
            </li>
            <li className="flex items-center gap-2.5">
              <Badge variant="secondary" className="shrink-0 bg-primary/10 text-primary">
                3
              </Badge>
              <span className="flex items-center gap-1.5">
                Semantic search
                <Search className="size-3.5 text-muted-foreground" />
              </span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
