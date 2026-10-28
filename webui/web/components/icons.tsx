// Drawn icons, one 1.5px stroke on a 16px grid.
type Props = { size?: number };

const frame = (size: number, children: preact.ComponentChildren) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="square"
    aria-hidden="true"
  >
    {children}
  </svg>
);

export const Chevron = ({ size = 12 }: Props) => frame(size, <path d="M6 3.5 10.5 8 6 12.5" />);

export const Sun = ({ size = 14 }: Props) =>
  frame(
    size,
    <>
      <rect x="5" y="5" width="6" height="6" />
      <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M3 13l1.4-1.4M11.6 4.4 13 3" />
    </>,
  );

export const Moon = ({ size = 14 }: Props) => frame(size, <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5Z" />);

export const Search = ({ size = 14 }: Props) =>
  frame(
    size,
    <>
      <rect x="2" y="2" width="9" height="9" />
      <path d="m11 11 3 3" />
    </>,
  );

export const Pulse = ({ size = 14 }: Props) => frame(size, <path d="M1 8h3l2-5 4 10 2-5h3" />);
