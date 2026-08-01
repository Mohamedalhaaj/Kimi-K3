/**
 * One icon vocabulary: 1.5px stroke, round caps, 16px grid, currentColor.
 * Inline rather than a package so the set stays consistent and adds no bytes
 * beyond what is used.
 */
type P = { className?: string };

const base = {
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const PlusIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 3.5v9M3.5 8h9" />
  </svg>
);

export const SearchIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <circle cx="7.2" cy="7.2" r="3.9" />
    <path d="m10.2 10.2 2.5 2.5" />
  </svg>
);

export const PinIcon = ({ className, filled }: P & { filled?: boolean }) => (
  <svg {...base} className={className} fill={filled ? "currentColor" : "none"}>
    <path d="M6.2 2.8h3.6l-.5 3.3 2 2.1H4.7l2-2.1-.5-3.3Z" />
    <path d="M8 8.2v5" />
  </svg>
);

export const TrashIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M3.2 4.4h9.6M6.4 4.4V3.2h3.2v1.2M4.6 4.4l.5 8h5.8l.5-8" />
  </svg>
);

export const PencilIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M11.1 2.9a1.3 1.3 0 0 1 1.9 1.9l-7 7-2.6.7.7-2.6 7-7Z" />
  </svg>
);

export const SendIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M2.8 8h9.6M8.6 4.2 12.4 8l-3.8 3.8" />
  </svg>
);

export const StopIcon = ({ className }: P) => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden className={className}>
    <rect x="4.5" y="4.5" width="7" height="7" rx="1.4" />
  </svg>
);

export const CopyIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <rect x="5.6" y="5.6" width="7.2" height="7.2" rx="1.4" />
    <path d="M10.4 5.6V4.2a1 1 0 0 0-1-1H4.2a1 1 0 0 0-1 1v5.2a1 1 0 0 0 1 1h1.4" />
  </svg>
);

export const RefreshIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M13 8a5 5 0 1 1-1.6-3.7" />
    <path d="M13 2.6V5h-2.4" />
  </svg>
);

export const SidebarIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <rect x="2.5" y="3" width="11" height="10" rx="1.6" />
    <path d="M6.4 3v10" />
  </svg>
);

export const SunIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <circle cx="8" cy="8" r="2.7" />
    <path d="M8 1.8v1.3M8 12.9v1.3M14.2 8h-1.3M3.1 8H1.8M12.4 3.6l-.9.9M4.5 11.5l-.9.9M12.4 12.4l-.9-.9M4.5 4.5l-.9-.9" />
  </svg>
);

export const MoonIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M13 9.4A5.4 5.4 0 0 1 6.6 3a5.5 5.5 0 1 0 6.4 6.4Z" />
  </svg>
);

export const AlertIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 2.8 14 13H2L8 2.8Z" />
    <path d="M8 6.6v2.8M8 11.2h.01" />
  </svg>
);

export const CheckIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="m3.4 8.4 3 3 6.2-6.8" />
  </svg>
);

export const ChevronIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
  </svg>
);

export const PaperclipIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M12.4 7.6 8 12a2.4 2.4 0 0 1-3.4-3.4l4.6-4.6a1.6 1.6 0 0 1 2.3 2.3l-4.6 4.6a.8.8 0 0 1-1.1-1.1l4.2-4.2" />
  </svg>
);

export const DownloadIcon = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 2.8v7.2M5.2 7.6 8 10.4l2.8-2.8M3.2 12.4h9.6" />
  </svg>
);
