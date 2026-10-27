// api.js van de editor laden (Keuze 32 en 33): een <script src> naar /office/ (dezelfde origin,
// dus de CSP van de app blijft), één keer per pagina. Mislukt het (geen verbinding, DS weg), dan
// probeert de volgende aanroep het opnieuw. Telt niet mee voor het budget: het komt van DS.

/** Wat de editor van DocsAPI.DocEditor teruggeeft (alleen wat wij gebruiken). */
export interface DocEditor {
  destroyEditor(): void;
  refreshFile?(config: Record<string, unknown>): void;
}

export interface DocsApi {
  DocEditor: new (placeholderId: string, config: Record<string, unknown>) => DocEditor;
}

/** Het deel van document en window dat het laden nodig heeft (los te testen). */
export interface ScriptHost {
  /** Een <script src> erbij; done(true) na load, done(false) bij een fout. Geeft de functie om
   * het element weer weg te halen. */
  inject(src: string, done: (ok: boolean) => void): () => void;
  docsApi(): DocsApi | undefined;
}

function browserHost(): ScriptHost {
  return {
    inject(src, done) {
      const el = document.createElement("script");
      el.src = src;
      el.async = true;
      el.addEventListener("load", () => done(true));
      el.addEventListener("error", () => done(false));
      document.head.appendChild(el);
      return () => el.remove();
    },
    docsApi: () => (window as unknown as { DocsAPI?: DocsApi }).DocsAPI,
  };
}

let loading: Promise<DocsApi> | null = null;

/** Laadt api.js (één keer) en geeft DocsAPI. */
export function loadApi(src: string, host: ScriptHost = browserHost()): Promise<DocsApi> {
  const ready = host.docsApi();
  if (ready) return Promise.resolve(ready);
  if (loading) return loading;
  const p = new Promise<DocsApi>((resolve, reject) => {
    let failed = false;
    let remove: (() => void) | null = null;
    remove = host.inject(src, (ok) => {
      const api = ok ? host.docsApi() : undefined;
      if (api) return resolve(api);
      failed = true;
      remove?.(); // de volgende poging zet een nieuw element neer
      reject(new Error(ok ? "api.js gaf geen DocsAPI" : "api.js kon niet laden"));
    });
    if (failed) remove();
  });
  loading = p;
  p.catch(() => {
    if (loading === p) loading = null; // de volgende keer opnieuw
  });
  return p;
}

/** Alleen voor tests: vergeten dat er al geladen werd. */
export function resetApiForTests(): void {
  loading = null;
}
