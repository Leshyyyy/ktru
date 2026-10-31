// Короткие имена для типов из контракта. Сами типы генерируются из contracts/openapi.yaml
// командой `npm run gen:api` — руками schema.d.ts не правим.
import type { components } from "./schema";

type S = components["schemas"];

export type ApiError = S["Error"];
export type ErrorCode = ApiError["error"]["code"];
export type ColumnRole = S["ColumnRole"];
export type ColumnInfo = S["ColumnInfo"];
export type Sheet = S["Sheet"];
export type FileInfo = S["FileInfo"];
export type LayoutHint = S["LayoutHint"];
export type JobCreate = S["JobCreate"];
export type Job = S["Job"];
export type JobStatus = S["JobStatus"];
export type Item = S["Item"];
export type ItemList = S["ItemList"];
export type ItemStatus = S["ItemStatus"];
export type ItemFlag = S["ItemFlag"];
export type Prediction = S["Prediction"];
export type CharacteristicMatch = S["CharacteristicMatch"];
export type ExtractedAttributes = S["ExtractedAttributes"];
export type FinalAnswer = S["FinalAnswer"];
export type CatalogItem = S["CatalogItem"];
export type CatalogVersion = S["CatalogVersion"];
export type ClassifyResult = S["ClassifyResult"];
export type QualityRun = S["QualityRun"];
export type QualityMetrics = S["QualityMetrics"];
export type Stats = S["Stats"];
export type Health = S["Health"];
export type PublicConfig = S["PublicConfig"];

export type Band = "green" | "yellow" | "red";

export type ColumnMappingEntry = JobCreate["column_mapping"][number];
