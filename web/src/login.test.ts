import { describe, expect, it } from "vitest";
import { resetCommand } from "./CantLogIn";

describe("resetCommand", () => {
  it("geeft het commando voor de beheerder, met de code erin", () => {
    expect(resetCommand("RESET-7KQ2-M9XD-4TNB-H3VA")).toBe(
      "docker exec -it $(docker ps -qf label=com.docker.compose.service=platform) " +
        "python -m app.cli reset-account RESET-7KQ2-M9XD-4TNB-H3VA",
    );
  });

  it("laat alleen letters, cijfers en streepjes door", () => {
    expect(resetCommand("RESET-AAAA'; rm -rf /")).toMatch(/reset-account RESET-AAAArm-rf$/);
  });
});
