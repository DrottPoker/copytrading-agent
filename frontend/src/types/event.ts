export type LiveEvent = {
  id?: string;
  type: string;
  channel: string;
  message: string;
  payload: Record<string, unknown>;
  createdAt?: string;
};

export type LiveEventListResponse = {
  items: LiveEvent[];
  total: number;
};
