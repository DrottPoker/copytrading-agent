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
  serverApiBaseUrl: process.env.SERVER_API_BASE_URL ?? config.serverApiBaseUrl ?? "http://127.0.0.1:8000",
  browserApiBaseUrl:
    process.env.NEXT_PUBLIC_BROWSER_API_BASE_URL ?? config.browserApiBaseUrl ?? "/api/backend",
  liveFeedPollMs: config.liveFeedPollMs ?? 5000,
  operationStatusPollMs: config.operationStatusPollMs ?? 5000,
  manualFillImportDays: config.manualFillImportDays ?? 30,
  manualFillImportMaxPages: config.manualFillImportMaxPages ?? 25,
  manualFillImportTargetFills: config.manualFillImportTargetFills ?? 10000,
  poolReimportBatchLimit: config.poolReimportBatchLimit ?? 50,
  poolReimportMaxBatches: config.poolReimportMaxBatches ?? 200,
};

export function getPublicApiBaseUrl() {
  return frontendConfig.browserApiBaseUrl;
}
