// Removing repeats from a list.

export function dedupe(items) {
  return [...new Set(items)];
}

export function count(items) {
  return dedupe(items).length;
}
