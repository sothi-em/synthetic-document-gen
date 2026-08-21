import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react"
import { Minus, Plus, RotateCcw } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

const MIN_SCALE = 0.2
const MAX_SCALE = 8
const ZOOM_STEP = 1.25

interface View {
  scale: number
  x: number
  y: number
}

const INITIAL_VIEW: View = { scale: 1, x: 0, y: 0 }

interface ImagePanZoomProps {
  src: string
  alt: string
  /** Key that resets the view when it changes (e.g. document id). */
  resetKey?: string | number
  /** Extra overlays rendered inside the viewport (e.g. a loading badge). */
  children?: ReactNode
  onError?: () => void
}

/**
 * Image viewport with pan and zoom: scroll to zoom (centered on the
 * cursor), drag to pan, and +/-/reset overlay buttons.
 */
export function ImagePanZoom({
  src,
  alt,
  resetKey,
  children,
  onError,
}: ImagePanZoomProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState<View>(INITIAL_VIEW)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{
    pointerX: number
    pointerY: number
    viewX: number
    viewY: number
  } | null>(null)

  useEffect(() => {
    setView(INITIAL_VIEW)
  }, [resetKey])

  /** Zoom by `factor`, keeping the point (cx, cy) fixed. */
  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      setView((v) => {
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor))
        const k = scale / v.scale
        return {
          scale,
          x: cx - (cx - v.x) * k,
          y: cy - (cy - v.y) * k,
        }
      })
    },
    [],
  )

  // Wheel zoom needs a non-passive listener so preventDefault works.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const cx = e.clientX - rect.left - rect.width / 2
      const cy = e.clientY - rect.top - rect.height / 2
      zoomAt(Math.exp(-e.deltaY * 0.0015), cx, cy)
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [zoomAt])

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = {
      pointerX: e.clientX,
      pointerY: e.clientY,
      viewX: view.x,
      viewY: view.y,
    }
    setDragging(true)
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) return
    setView((v) => ({
      ...v,
      x: drag.viewX + (e.clientX - drag.pointerX),
      y: drag.viewY + (e.clientY - drag.pointerY),
    }))
  }

  const endDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragRef.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative flex min-w-0 flex-1 items-center justify-center overflow-hidden p-4",
        dragging ? "cursor-grabbing" : "cursor-grab",
      )}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        className="max-h-full max-w-full select-none object-contain will-change-transform"
        style={{
          transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})`,
        }}
        onError={onError}
      />
      {children}
      <div
        className="absolute right-3 top-3 z-10 flex flex-col gap-1"
        onPointerDown={(e) => e.stopPropagation()}
      >
        <Button
          size="icon"
          variant="outline"
          className="h-7 w-7"
          title="Zoom in"
          aria-label="Zoom in"
          onClick={() => zoomAt(ZOOM_STEP, 0, 0)}
        >
          <Plus className="size-4" />
        </Button>
        <Button
          size="icon"
          variant="outline"
          className="h-7 w-7"
          title="Zoom out"
          aria-label="Zoom out"
          onClick={() => zoomAt(1 / ZOOM_STEP, 0, 0)}
        >
          <Minus className="size-4" />
        </Button>
        <Button
          size="icon"
          variant="outline"
          className="h-7 w-7"
          title="Reset view"
          aria-label="Reset view"
          onClick={() => setView(INITIAL_VIEW)}
        >
          <RotateCcw className="size-3.5" />
        </Button>
      </div>
      <span className="absolute bottom-2 right-3 z-10 rounded bg-background/80 px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground">
        {Math.round(view.scale * 100)}%
      </span>
    </div>
  )
}
