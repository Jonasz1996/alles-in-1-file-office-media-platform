// De route /sport/:id? (één chunk): zonder id het overzicht, met id één activiteit.

import type { JSX } from "react";
import type { RouteProps } from "../router";
import ActivityPage from "./ActivityPage";
import SportHome from "./SportHome";

export default function SportPage(props: RouteProps): JSX.Element {
  return props.params.id ? <ActivityPage key={props.params.id} {...props} /> : <SportHome {...props} />;
}
