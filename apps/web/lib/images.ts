/**
 * Preparing images for the chat request.
 *
 * Resizing happens in the browser: a modern phone photo is 4-12 MB, and sending
 * that as base64 would inflate it by a third again for no benefit. The long
 * edge is capped and the aspect ratio preserved — the brief forbids distorting
 * or silently dropping attachments.
 */

export const MAX_IMAGES = 4;
export const MAX_SOURCE_BYTES = 20 * 1024 * 1024;
const MAX_EDGE = 1536;
const QUALITY = 0.82;

export interface PreparedImage {
  id: string;
  name: string;
  dataUrl: string;
  width: number;
  height: number;
  bytes: number;
}

export function isSupportedImage(file: File): boolean {
  return /^image\/(png|jpeg|jpg|webp|gif|avif)$/i.test(file.type);
}

async function loadBitmap(file: File): Promise<ImageBitmap | HTMLImageElement> {
  if ("createImageBitmap" in window) {
    return await createImageBitmap(file);
  }
  // Safari fallback.
  const url = URL.createObjectURL(file);
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("could not decode image"));
      img.src = url;
    });
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

export async function prepareImage(file: File): Promise<PreparedImage> {
  if (!isSupportedImage(file)) {
    throw new Error(`${file.name}: that file type is not a supported image.`);
  }
  if (file.size > MAX_SOURCE_BYTES) {
    throw new Error(`${file.name}: image is larger than 20 MB.`);
  }

  const bitmap = await loadBitmap(file);
  const sw = "width" in bitmap ? bitmap.width : 0;
  const sh = "height" in bitmap ? bitmap.height : 0;
  if (!sw || !sh) throw new Error(`${file.name}: image has no dimensions.`);

  // Never upscale, and keep the aspect ratio exactly.
  const scale = Math.min(1, MAX_EDGE / Math.max(sw, sh));
  const width = Math.max(1, Math.round(sw * scale));
  const height = Math.max(1, Math.round(sh * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error(`${file.name}: could not process image.`);
  ctx.drawImage(bitmap as CanvasImageSource, 0, 0, width, height);
  if ("close" in bitmap) bitmap.close();

  // PNG keeps transparency; everything else is cheaper as JPEG.
  const type = file.type === "image/png" ? "image/png" : "image/jpeg";
  const dataUrl = canvas.toDataURL(type, QUALITY);

  return {
    id: `${file.name}-${file.size}-${Math.round(width * height)}`,
    name: file.name,
    dataUrl,
    width,
    height,
    // base64 carries ~4 bytes per 3 bytes of payload.
    bytes: Math.round((dataUrl.length - dataUrl.indexOf(",") - 1) * 0.75),
  };
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
