import { describe, expect, it } from "vitest";
import {
  MAX_FILE_SIZE,
  formatBytes,
  validateAudioFile,
} from "@/lib/file";

function testFile(name: string, size: number): File {
  return { name, size } as File;
}

describe("formatBytes", () => {
  it("formats byte values for UI hints", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(500 * 1024 * 1024)).toBe("500.0 MB");
  });
});

describe("validateAudioFile", () => {
  it("accepts supported formats case-insensitively", () => {
    expect(validateAudioFile(testFile("meeting.MP3", 1024))).toBeNull();
    expect(validateAudioFile(testFile("interview.webm", 1024))).toBeNull();
  });

  it("rejects unsupported, empty, and oversized files", () => {
    expect(validateAudioFile(testFile("notes.txt", 1024))).toContain("不支持");
    expect(validateAudioFile(testFile("empty.wav", 0))).toContain("为空");
    expect(validateAudioFile(testFile("large.wav", MAX_FILE_SIZE + 1))).toContain(
      "大于",
    );
  });
});
