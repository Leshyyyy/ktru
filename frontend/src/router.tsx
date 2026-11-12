import { createBrowserRouter, createHashRouter, type RouteObject } from "react-router";
import { Layout } from "./components/Layout";
import { UploadPage } from "./pages/UploadPage";
import { JobsPage } from "./pages/JobsPage";
import { JobPage } from "./pages/JobPage";
import { BatchPage } from "./pages/BatchPage";
import { QualityPage } from "./pages/QualityPage";
import { NotFoundPage } from "./pages/NotFoundPage";

const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { path: "/", element: <UploadPage /> },
      { path: "/jobs", element: <JobsPage /> },
      { path: "/jobs/:jobId", element: <JobPage /> },
      { path: "/batches/:batchId", element: <BatchPage /> },
      { path: "/quality", element: <QualityPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

// Демо-сборка открывается как статическая страница без сервера, поэтому там адреса через #.
export const router = import.meta.env.VITE_ROUTER === "hash" ? createHashRouter(routes) : createBrowserRouter(routes);
