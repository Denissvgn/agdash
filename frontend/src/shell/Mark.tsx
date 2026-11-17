/**
 * The agdash mark: the small drawing of the app icon, which is also the page's icon.
 * It sits beside the name, which names it, so it says nothing of its own.
 */
export function Mark() {
  return <img alt="" className="size-[24px] shrink-0" height={24} src={`${import.meta.env.BASE_URL}favicon.svg`} width={24} />
}
