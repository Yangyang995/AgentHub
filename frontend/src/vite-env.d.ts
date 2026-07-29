/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PROJECT_ID?: string
  readonly VITE_AGENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
