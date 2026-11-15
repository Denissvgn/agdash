import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import App from './App.tsx'

test('renders the shell with the product name', () => {
  window.location.hash = ''
  render(<App />)

  expect(screen.getByRole('link', { name: 'agdash, go to the live board' })).toHaveTextContent('agdash')
  expect(screen.getByRole('main')).toBeInTheDocument()
})
