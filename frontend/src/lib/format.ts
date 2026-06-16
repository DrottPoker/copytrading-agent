export function formatInteger(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 }).format(numberValue(value));
}

export function formatCompact(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 1,
    notation: "compact",
  }).format(numberValue(value));
}

export function formatCurrency(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    currency: "USD",
    maximumFractionDigits: 2,
    style: "currency",
  }).format(numberValue(value));
}

export function formatPercent(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 1,
    style: "percent",
  }).format(numberValue(value));
}

export function formatScore(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(numberValue(value));
}

export function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("sv-SE", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatMs(value: number | null | undefined) {
  if (!value) {
    return "-";
  }
  return formatDate(new Date(value).toISOString());
}

export function formatBytes(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(size)} ${
    units[unitIndex]
  }`;
}

export function numberValue(value: string | number) {
  return typeof value === "number" ? value : Number(value);
}
