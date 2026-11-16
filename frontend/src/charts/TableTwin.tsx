import { useTranslation } from 'react-i18next'

export type TableTwinColumn = { key: string; label: string }
export type TableTwinRow = { key: string; header: string; cells: (string | null)[] }
/** The same values a figure draws, as a table, so nothing is only drawn. */
export type TableTwinData = { rowHeader: string; columns: TableTwinColumn[]; rows: TableTwinRow[] }

/**
 * A figure's table twin, behind a disclosure. A value that was not reported is a
 * dash with "not reported" for assistive technology, as in the ledger.
 */
export function TableTwin({ title, rowHeader, columns, rows }: TableTwinData & { title: string }) {
  const { t } = useTranslation()
  return (
    <details className="group text-label">
      <summary className="w-fit cursor-pointer rounded-compact text-metadata font-medium text-secondary-ink hover:text-primary-ink">
        {t('charts.table.show')}
      </summary>
      <div
        aria-label={t('charts.table.region', { title })}
        className="mt-sm max-h-[20rem] overflow-auto rounded-surface border border-hairline"
        role="region"
        tabIndex={0}
      >
        <table className="w-full border-collapse text-left tabular-nums">
          <caption className="sr-only">{title}</caption>
          <thead className="sticky top-0 bg-working-paper">
            <tr className="border-b border-hairline">
              <th className="whitespace-nowrap px-sm py-xs text-label font-medium text-secondary-ink" scope="col">
                {rowHeader}
              </th>
              {columns.map((column) => (
                <th key={column.key} className="whitespace-nowrap px-sm py-xs text-right text-label font-medium text-secondary-ink" scope="col">
                  <span data-figure-data="">{column.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-b border-hairline last:border-b-0">
                <th className="whitespace-nowrap px-sm py-xs font-normal text-primary-ink" scope="row">
                  <span data-figure-data="">{row.header}</span>
                </th>
                {row.cells.map((cell, i) => (
                  <td key={columns[i]?.key ?? i} className="whitespace-nowrap px-sm py-xs text-right text-primary-ink">
                    {cell === null ? (
                      <>
                        <span aria-hidden="true" className="text-secondary-ink">
                          {t('charts.table.dash')}
                        </span>
                        <span className="sr-only">{t('charts.table.notReported')}</span>
                      </>
                    ) : (
                      <span data-figure-data="">{cell}</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
