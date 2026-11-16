import { within } from '@testing-library/react'

// Every role a person can operate, and the scrolling regions they can focus.
const ROLES = [
  'button',
  'link',
  'textbox',
  'searchbox',
  'combobox',
  'listbox',
  'checkbox',
  'radio',
  'switch',
  'slider',
  'spinbutton',
  'tab',
  'menuitem',
  'dialog',
] as const

/**
 * Controls under ``root`` that assistive technology would announce without a name,
 * computed the way the accessibility tree computes them. Empty when every one has a name.
 */
export function unnamedControls(root: HTMLElement): string[] {
  const found: string[] = []
  for (const role of ROLES) {
    const named = new Set(within(root).queryAllByRole(role, { name: /\S/ }))
    for (const element of within(root).queryAllByRole(role)) {
      if (!named.has(element)) found.push(`${role}: ${element.outerHTML.slice(0, 160)}`)
    }
  }
  for (const element of root.querySelectorAll<HTMLElement>('[tabindex]:not([tabindex="-1"])')) {
    const name = element.getAttribute('aria-label') ?? element.getAttribute('aria-labelledby')
    if (!name && !element.matches('a, button, input, select, textarea, summary')) {
      found.push(`focusable: ${element.outerHTML.slice(0, 160)}`)
    }
  }
  return found
}
