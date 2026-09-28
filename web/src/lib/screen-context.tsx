import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react"

/** A company as it appears on screen (id + display fields only). */
export interface ScreenCompany {
  id: number
  name: string
  industry: string
}

/** A document as it appears on screen (id + display fields only). */
export interface ScreenDocument {
  id: number
  filename: string
  company_id: number
  filetype: string
}

/**
 * Serialized description of the current UI state, sent with every agent
 * chat message so it can resolve "this company" / "these documents" to
 * concrete ids.
 */
export interface ScreenState {
  /** Active tab id (overview|companies|labels|document-types|documents|settings). */
  activeTab: string
  /** Selected company id, or null when nothing is selected. */
  selectedCompanyId: number | null
  /** The selected company's display fields, or null. */
  selectedCompany: ScreenCompany | null
  /** Companies currently visible in the Companies tab table. */
  visibleCompanies: ScreenCompany[]
  /** Documents currently visible in the Documents tab table. */
  visibleDocuments: ScreenDocument[]
}

interface ScreenContextValue {
  screen: ScreenState
  /** Merge a partial update into the current screen state. */
  report: (partial: Partial<ScreenState>) => void
}

const ScreenContext = createContext<ScreenContextValue | null>(null)

const DEFAULT_SCREEN: ScreenState = {
  activeTab: "overview",
  selectedCompanyId: null,
  selectedCompany: null,
  visibleCompanies: [],
  visibleDocuments: [],
}

/**
 * Provides the current screen state to the app tree. Panels report
 * slices of state (visible rows, selection) as the user interacts; the
 * assistant panel reads the merged state when sending a message.
 */
export function ScreenProvider({ children }: { children: ReactNode }) {
  const [screen, setScreen] = useState<ScreenState>(DEFAULT_SCREEN)

  const report = useCallback((partial: Partial<ScreenState>) => {
    setScreen((prev) => ({ ...prev, ...partial }))
  }, [])

  const value = useMemo(() => ({ screen, report }), [screen, report])

  return (
    <ScreenContext.Provider value={value}>{children}</ScreenContext.Provider>
  )
}

/**
 * Read the current screen state and report changes into it.
 *
 * @returns The merged screen state and a `report` merge function.
 * @throws When used outside a `ScreenProvider`.
 */
export function useScreenContext(): ScreenContextValue {
  const ctx = useContext(ScreenContext)
  if (!ctx) {
    throw new Error("useScreenContext must be used within a ScreenProvider")
  }
  return ctx
}
