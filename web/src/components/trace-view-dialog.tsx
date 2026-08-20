import { useEffect, useState } from "react"
import JsonView from "@uiw/react-json-view"
import { githubDarkTheme } from "@uiw/react-json-view/githubDark"
import { githubLightTheme } from "@uiw/react-json-view/githubLight"
import { Download } from "lucide-react"

import { type DocumentRecord } from "@/lib/api"
import { getStoredMode } from "@/lib/theme"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface TraceViewDialogProps {
  /** The document whose trace is shown; ``null`` closes the dialog. */
  doc: DocumentRecord | null
  onClose: () => void
}

/**
 * Modal displaying a document's per-stage generation trace
 * (``gen_tracing``) with a collapsible/expandable JSON tree.
 *
 * The tree starts collapsed to the second level (stage objects visible,
 * their prompt/output payloads folded) so the overview stays readable;
 * any node can be expanded individually. The dialog is sized to fit
 * the tree (width follows the content, clamped to the viewport; the
 * body scrolls vertically beyond 70vh). A footer button downloads the
 * trace as a JSON file.
 */

/** Trigger a client-side download of the document's trace as JSON. */
function downloadTrace(doc: DocumentRecord): void {
  const blob = new Blob([JSON.stringify(doc.gen_tracing ?? null, null, 2)], {
    type: "application/json",
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${doc.filename.replace(/\.[^.]+$/, "")}_trace.json`
  anchor.click()
  URL.revokeObjectURL(url)
}
export function TraceViewDialog({ doc, onClose }: TraceViewDialogProps) {
  const [isDark, setIsDark] = useState(false)

  // Pick the JSON tree theme from the app's light/dark mode when opened.
  useEffect(() => {
    if (doc) setIsDark(getStoredMode() === "dark")
  }, [doc])

  return (
    <Dialog open={doc !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-fit max-w-[min(90vw,72rem)] sm:max-w-[min(90vw,72rem)]">
        <DialogHeader>
          <DialogTitle>Generation trace — {doc?.filename}</DialogTitle>
          <DialogDescription className="mt-2">
            Per-stage prompts, outputs and timings captured while this
            document was generated. Click any node to expand or collapse
            it.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[65vh] overflow-auto rounded-md border bg-background">
          <JsonView
            value={doc?.gen_tracing ?? {}}
            style={isDark ? githubDarkTheme : githubLightTheme}
            collapsed={2}
            displayDataTypes={false}
            enableClipboard={false}
          />
        </div>
        {doc && (
          <DialogFooter>
            <Button variant="outline" onClick={() => downloadTrace(doc)}>
              <Download />
              Download trace (JSON)
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
