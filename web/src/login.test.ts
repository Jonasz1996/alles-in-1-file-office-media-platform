import { describe, expect, it } from "vitest";
import { resetCommand } from "./CantLogIn";

describe("resetCommand", () => {
  it("geeft het commando voor de beheerder, met het adres in kleine letters", () => {
    expect(resetCommand("  Jonas@Voorbeeld.be ")).toBe(
      "docker exec -it $(docker ps -qf label=com.docker.compose.service=platform) " +
        "python -m app.cli reset-account 'jonas@voorbeeld.be'",
    );
  });

  it("zet een aanhalingsteken veilig tussen enkele aanhalingstekens", () => {
    expect(resetCommand("o'neil@x.be; rm -rf /")).toMatch(/reset-account 'o'\\''neil@x\.be; rm -rf \/'$/);
  });
});
