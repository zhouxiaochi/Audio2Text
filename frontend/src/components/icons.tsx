import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="24"
      viewBox="0 0 24 24"
      width="24"
      {...props}
    >
      {children}
    </svg>
  );
}

export function WaveIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path
        d="M3 12h2l2.2-7 3.2 14 2.8-10 2.1 6H21"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </IconBase>
  );
}

export function UploadIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
      <path d="M5 14.5v3A2.5 2.5 0 0 0 7.5 20h9a2.5 2.5 0 0 0 2.5-2.5v-3" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
    </IconBase>
  );
}

export function FileAudioIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M6 3h7l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.7" />
      <path d="M13 3v5h5M8 15h1.2l1.2-3 2 6 1.5-4H16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </IconBase>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="m5 12.5 4.2 4L19 7" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
    </IconBase>
  );
}

export function DownloadIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </IconBase>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M19 8a8 8 0 1 0 1 6" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
      <path d="M19 3v5h-5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </IconBase>
  );
}

export function SaveIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M5 3h11l3 3v15H5V3Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.7" />
      <path d="M8 3v6h8V3M8 21v-7h8v7" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.7" />
    </IconBase>
  );
}

export function ErrorIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 7v6m0 4h.01" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
    </IconBase>
  );
}
