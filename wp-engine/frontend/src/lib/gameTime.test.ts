import { describe, expect, it } from "vitest";

import { clockToElapsedMinutes, quarterBreaks, totalMinutes } from "./gameTime";

describe("clockToElapsedMinutes", () => {
  it("maps tipoff to 0", () => {
    expect(clockToElapsedMinutes(1, "Q1 12:00")).toBe(0);
  });

  it("maps mid-quarter clocks", () => {
    // Q1 with 9:30 left = 2.5 minutes elapsed
    expect(clockToElapsedMinutes(1, "Q1 09:30")).toBeCloseTo(2.5);
  });

  it("maps end of regulation to 48", () => {
    expect(clockToElapsedMinutes(4, "Q4 00:00")).toBe(48);
  });

  it("maps overtime past 48 with 5-minute periods", () => {
    expect(clockToElapsedMinutes(5, "OT1 05:00")).toBe(48);
    expect(clockToElapsedMinutes(5, "OT1 00:45")).toBeCloseTo(52.25);
    expect(clockToElapsedMinutes(6, "OT2 05:00")).toBe(53);
  });

  it("parses the clock digits regardless of the label", () => {
    expect(clockToElapsedMinutes(4, "Q4 02:31")).toBeCloseTo(45.4833, 3);
  });
});

describe("axis helpers", () => {
  it("regulation has quarter breaks at 12/24/36/48", () => {
    expect(quarterBreaks(4)).toEqual([12, 24, 36, 48]);
    expect(totalMinutes(4)).toBe(48);
  });

  it("overtime extends the axis by 5 per OT", () => {
    expect(quarterBreaks(6)).toEqual([12, 24, 36, 48, 53, 58]);
    expect(totalMinutes(6)).toBe(58);
  });
});
