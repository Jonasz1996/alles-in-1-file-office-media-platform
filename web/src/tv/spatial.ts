// Bediening met pijltjes (fase 11, stap 9): van het element met focus naar het dichtstbijzijnde
// element in die richting. Puur rekenwerk op rechthoeken, zodat het te testen is zonder browser.

export type Dir = "up" | "down" | "left" | "right";

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** De score van `to` vanaf `from` in richting `dir` (lager is beter), of null als `to` niet in
 * die richting ligt. Afstand langs de richting telt één keer, de afwijking ernaast dubbel; wie
 * ernaast overlapt (dezelfde rij of kolom), krijgt geen afwijking. */
export function score(from: Box, to: Box, dir: Dir): number | null {
  const fx = (from.left + from.right) / 2;
  const fy = (from.top + from.bottom) / 2;
  const tx = (to.left + to.right) / 2;
  const ty = (to.top + to.bottom) / 2;
  let along: number;
  let beside: number;
  if (dir === "right" || dir === "left") {
    along = dir === "right" ? to.left - from.right : from.left - to.right;
    if ((dir === "right" ? tx - fx : fx - tx) <= 1) return null;
    const overlap = Math.min(from.bottom, to.bottom) - Math.max(from.top, to.top);
    beside = overlap > 0 ? 0 : Math.abs(ty - fy);
  } else {
    along = dir === "down" ? to.top - from.bottom : from.top - to.bottom;
    if ((dir === "down" ? ty - fy : fy - ty) <= 1) return null;
    const overlap = Math.min(from.right, to.right) - Math.max(from.left, to.left);
    beside = overlap > 0 ? Math.abs(tx - fx) * 0.1 : Math.abs(tx - fx);
  }
  return Math.max(0, along) + 2 * beside;
}

/** De index van het beste doel, of -1. */
export function best(from: Box, boxes: readonly Box[], dir: Dir): number {
  let found = -1;
  let low = Infinity;
  boxes.forEach((b, i) => {
    const s = score(from, b, dir);
    if (s !== null && s < low) {
      low = s;
      found = i;
    }
  });
  return found;
}
