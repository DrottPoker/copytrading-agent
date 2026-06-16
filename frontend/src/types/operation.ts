export type OperationStatus = {
  key: string;
  label: string;
  status: string;
  startedAt: string | null;
  completedAt: string | null;
  updatedAt: string | null;
  lastSuccessAt: string | null;
  durationMs: number | null;
  lastError: string | null;
  payload: Record<string, unknown>;
};

export type OperationStatusListResponse = {
  items: OperationStatus[];
};
