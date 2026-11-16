// The specimen's own copy lives beside it, not in the app's catalogue, so the built
// app carries none of it. The specimen and its tests add it to a catalogue here.
import { catalogue, createI18n, LANGUAGE } from '../../i18n'
import copy from './copy.json'

type Tree = { [key: string]: string | Tree }

export function withSpecimen(translation: Tree = catalogue as Tree): Tree {
  return { ...translation, charts: { ...(translation.charts as Tree), ...copy.charts } }
}

export const specimenI18n = (translation?: Tree) => createI18n({ [LANGUAGE]: { translation: withSpecimen(translation) } })
