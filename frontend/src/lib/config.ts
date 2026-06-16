import appConfig from "../../config/app.json";

type FrontendAppConfig = {
  serverApiBaseUrl?: string;
  browserApiBaseUrl?: string;
  liveFeedPollMs?: number;
  operationStatusPollMs?: number;
  manualFillImportDays?: number;
  manualFillImportMaxPages?: number;
  manualFillImportTargetFills?: number;
  poolReimportBatchLimit?: number;
  poolReimportMaxBatches?: number;
};

const config = appConfig as FrontendAppConfig;

export const frontendConfig = {
  serverApiBaseUrl: config.serverApiBaseUrl ?? "http://127.0.0.1:8000",
  browserApiBaseUrl: config.browserApiBaseUrl ?? "http://127.0.0.1:8000",
  liveFeedPollMs: config.liveFeedPollMs ?? 5000,
  operationStatusPollMs: config.operationStatusPollMs ?? 5000,
  manualFillImportDays: config.manualFillImportDays ?? 30,
  manualFillImportMaxPages: config.manualFillImportMaxPages ?? 25,
  manualFillImportTargetFills: config.manualFillImportTargetFills ?? 10000,
  poolReimportBatchLimit: config.poolReimportBatchLimit ?? 50,
  poolReimportMaxBatches: config.poolReimportMaxBatches ?? 200,
};
