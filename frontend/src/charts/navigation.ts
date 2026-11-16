/**
 * Up and down for figures laid out in rows (lanes, strips): move to the mark on the
 * row above or below that is nearest in position along the axis.
 */
export function rowNeighbours(rowOf: number[], positionOf: number[]) {
  return (from: number, key: string): number | undefined => {
    if ((key !== 'ArrowUp' && key !== 'ArrowDown') || from >= rowOf.length) return undefined
    const row = rowOf[from] + (key === 'ArrowDown' ? 1 : -1)
    let best: number | undefined
    rowOf.forEach((r, i) => {
      if (r !== row) return
      if (best === undefined || Math.abs(positionOf[i] - positionOf[from]) < Math.abs(positionOf[best] - positionOf[from])) best = i
    })
    return best ?? from
  }
}
