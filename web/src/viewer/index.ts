// De viewer (frozen na P0): lazy, met het woordenboek "viewer". P4 vervangt Viewer.tsx.

import { lazyWithAreas } from "../i18n";
import type { ViewerProps } from "./types";

export type { ViewerItem, ViewerProps, ViewerSource } from "./types";
export { useViewerParam, VIEWER_PARAM } from "./param";

export const Viewer = lazyWithAreas<ViewerProps>(() => import("./Viewer"), ["viewer"]);
