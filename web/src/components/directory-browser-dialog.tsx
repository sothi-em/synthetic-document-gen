import { useCallback, useEffect, useState } from "react"
import { ArrowUp, Folder, FolderOpen, LoaderCircle } from "lucide-react"

import { api, type BrowseResult } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface DirectoryBrowserDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Directory to start in (defaults to the server's home directory). */
  initialPath?: string | null
  /** Called with the selected directory path. */
  onSelect: (path: string) => void
}

/**
 * Interactive directory tree: navigate the server's filesystem
 * (subdirectories only) and select one directory. Backed by
 * `GET /api/fs/browse`.
 */
export function DirectoryBrowserDialog({
  open,
  onOpenChange,
  initialPath,
  onSelect,
}: DirectoryBrowserDialogProps) {
  const [current, setCurrent] = useState<BrowseResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (path?: string) => {
    setLoading(true)
    setError(null)
    try {
      setCurrent(await api.browse(path))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load(initialPath ?? undefined)
  }, [open, initialPath, load])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Select directory</DialogTitle>
          <DialogDescription>
            Navigate the server's filesystem and pick the document output
            directory.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!current?.parent || loading}
              onClick={() => current?.parent && void load(current.parent)}
            >
              <ArrowUp />
              Up
            </Button>
            <code
              className="min-w-0 flex-1 truncate rounded-md bg-secondary px-2.5 py-1.5 text-xs"
              title={current?.path}
            >
              {current?.path ?? "…"}
            </code>
          </div>
          <div className="max-h-72 overflow-y-auto rounded-lg border">
            {loading && (
              <p className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" />
                Loading…
              </p>
            )}
            {error && <p className="p-3 text-sm text-destructive">{error}</p>}
            {current && !loading && current.entries.length === 0 && (
              <p className="p-3 text-sm text-muted-foreground">
                No subdirectories here.
              </p>
            )}
            {current &&
              !loading &&
              current.entries.map((entry) => (
                <button
                  key={entry.path}
                  type="button"
                  onClick={() => void load(entry.path)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  <Folder
                    className="size-4 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                  <span className="truncate">{entry.name}</span>
                </button>
              ))}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!current}
            onClick={() => {
              if (current) {
                onSelect(current.path)
                onOpenChange(false)
              }
            }}
          >
            <FolderOpen />
            Select this folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
