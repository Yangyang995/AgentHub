import { createBrowserRouter } from 'react-router-dom'

import { WorkbenchPage } from '../routes/workbench-page'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <WorkbenchPage />,
  },
])

