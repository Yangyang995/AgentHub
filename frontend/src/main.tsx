import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'

import { queryClient } from './app/query-client'
import { router } from './app/router'
import './styles/global.css'

const rootElement = document.getElementById('root')

if (rootElement === null) {
  throw new Error('无法找到应用挂载节点')
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)

