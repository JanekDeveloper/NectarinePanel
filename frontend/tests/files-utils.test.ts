import { describe, expect, it } from "vitest";

import {
  isBrowsableDirectory,
  isBrowsableFile,
  persistentMounts,
} from "../utils/files";

describe("file manager entry utilities", () => {
  it("opens regular and internal symlink directories", () => {
    expect(isBrowsableDirectory({ kind: "directory" })).toBe(true);
    expect(
      isBrowsableDirectory({
        kind: "symlink",
        target_kind: "directory",
      }),
    ).toBe(true);
  });

  it("does not browse broken or external symlinks", () => {
    expect(isBrowsableDirectory({ kind: "symlink" })).toBe(false);
    expect(
      isBrowsableDirectory({ kind: "symlink", target_kind: "file" }),
    ).toBe(false);
  });

  it("recognizes regular and internal symlink files", () => {
    expect(isBrowsableFile({ kind: "file" })).toBe(true);
    expect(
      isBrowsableFile({ kind: "symlink", target_kind: "file" }),
    ).toBe(true);
    expect(
      isBrowsableFile({ kind: "symlink", target_kind: "directory" }),
    ).toBe(false);
  });

  it("returns only valid persistent mount mappings", () => {
    expect(
      persistentMounts({
        persistent_mounts: [
          { source: "data", target: "/app/data", read_only: false },
          { source: "cache", target: "relative" },
          null,
        ],
      }),
    ).toEqual([
      { source: "data", target: "/app/data", read_only: false },
    ]);
    expect(persistentMounts({})).toEqual([]);
  });
});
