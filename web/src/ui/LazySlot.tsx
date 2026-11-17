// Een plek voor een lazy onderdeel van een later pakket (fase 6, Z0): de rij Plek en Personen in
// het infopaneel, de waarschuwing in het deeldialoog. Laden toont niets (geen "Laden…" in een
// paneel dat er al staat), en lukt het chunk niet (geen verbinding, een oude versie na een
// update), dan blijft de plek leeg: het paneel of dialoog eromheen blijft gewoon werken.

import { Component, type JSX, type ReactNode, Suspense } from "react";

class Quiet extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

export function LazySlot({ children }: { children: ReactNode }): JSX.Element {
  return (
    <Quiet>
      <Suspense fallback={null}>{children}</Suspense>
    </Quiet>
  );
}
