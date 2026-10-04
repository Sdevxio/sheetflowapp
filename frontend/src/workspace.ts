const IMPORT_KEY = "sheetflow.import";
const SHEET_KEY = "sheetflow.sheet";

export function rememberedImport(): string {
  return localStorage.getItem(IMPORT_KEY) ?? "";
}

export function rememberedSheet(): string {
  return localStorage.getItem(SHEET_KEY) ?? "";
}

export function rememberImport(id: string, sheet?: string) {
  if (id) localStorage.setItem(IMPORT_KEY, id);
  if (sheet) localStorage.setItem(SHEET_KEY, sheet);
}

export function forgetImport(id?: string) {
  const current = localStorage.getItem(IMPORT_KEY) ?? "";
  if (id && current && current !== id) return;
  localStorage.removeItem(IMPORT_KEY);
  localStorage.removeItem(SHEET_KEY);
  window.dispatchEvent(new Event("sheetflow-workspace"));
}
