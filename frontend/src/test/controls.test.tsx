import { render } from '@testing-library/react'
import { expect, test } from 'vitest'
import { unnamedControls } from './controls'

test('controls without a name are found, named ones are not', () => {
  const { container } = render(
    <div>
      <button type="button">Save</button>
      <button aria-label="Close" type="button">
        <svg aria-hidden="true" />
      </button>
      <button type="button">
        <svg aria-hidden="true" />
      </button>
      <a href="#/x">
        <svg aria-hidden="true" />
      </a>
      <label htmlFor="named">Folder</label>
      <input id="named" />
      <input />
      <select>
        <option value="">Choose</option>
      </select>
      {/* Deliberately wrong: the check must catch a focusable element with no name. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <div tabIndex={0}>scroll me</div>
      <div aria-label="Timeline" role="region" tabIndex={0} />
    </div>,
  )
  const found = unnamedControls(container)
  expect(found.map((entry) => entry.split(':')[0])).toEqual(['button', 'link', 'textbox', 'combobox', 'focusable'])
})
