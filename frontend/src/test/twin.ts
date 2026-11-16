import { act } from '@testing-library/react'

/** Opens a figure's table twin, as a person would, so a long one builds its rows. */
export function openTwin(root: ParentNode = document) {
  for (const twin of root.querySelectorAll('details')) {
    act(() => {
      twin.open = true
      twin.dispatchEvent(new Event('toggle'))
    })
  }
}
