export const MAX_FILE_SIZE = 300 * 1024 * 1024;
export const ACCEPTED_EXTENSIONS = [
  ".mp3",
  ".wav",
  ".m4a",
  ".mp4",
  ".mpeg",
  ".mpga",
  ".webm",
  ".ogg",
  ".flac",
] as const;

export const FILE_ACCEPT =
  "audio/*,video/mp4,video/webm,.mp3,.wav,.m4a,.mp4,.mpeg,.mpga,.webm,.ogg,.flac";

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function validateAudioFile(file: File): string | null {
  const extension = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (!ACCEPTED_EXTENSIONS.includes(extension as (typeof ACCEPTED_EXTENSIONS)[number])) {
    return `不支持 ${extension} 格式，请选择常见音频或视频文件。`;
  }
  if (file.size > MAX_FILE_SIZE) {
    return `文件大于 ${formatBytes(MAX_FILE_SIZE)}，请选择更小的文件。`;
  }
  if (file.size === 0) return "文件为空，请重新选择。";
  return null;
}
