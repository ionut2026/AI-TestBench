import { describe, expect, it } from 'vitest'
import { checkFilter, describeFilter, entryMatches, matches } from '../src/messageFilter'
import type { TimelineEntry } from '../src/testbench'

const entry = (name: string, body: unknown, extra: Partial<TimelineEntry> = {}): TimelineEntry => ({
  id: 10,
  time: 0,
  way: 'rx',
  topic: '/is/iw/tx',
  name,
  analyzer: -1,
  payload: { Version: 7, [name]: body },
  raw: '',
  validation: { valid: true, errors: [] },
  ...extra,
})

describe('matches', () => {
  it('matches a partial object and ignores extra keys', () => {
    expect(matches({ PreviousState: 'Idle', CurrentState: 'E-Stop' }, { CurrentState: 'E-Stop' })).toBe(true)
    expect(matches({ CurrentState: 'Idle' }, { CurrentState: 'E-Stop' })).toBe(false)
  })

  it('matches nested objects and arrays element-wise', () => {
    const body = { Module: { Status: 'Ready' }, EventArgs: ['a', 'b'] }
    expect(matches(body, { Module: { Status: 'Ready' } })).toBe(true)
    expect(matches(body, { EventArgs: ['a', 'b'] })).toBe(true)
    expect(matches(body, { EventArgs: ['a'] })).toBe(false)
  })

  it('supports operators', () => {
    const body = { EventId: 15859714, Severity: 'Warning', Message: 'connection lost', EventArgs: [] as unknown[] }
    expect(matches(body, { Severity: { $in: ['Warning', 'CriticalError'] } })).toBe(true)
    expect(matches(body, { Severity: { $nin: ['Warning'] } })).toBe(false)
    expect(matches(body, { Message: { $regex: 'connection' } })).toBe(true)
    expect(matches(body, { EventId: { $gt: 1, $lte: 15859714 } })).toBe(true)
    expect(matches(body, { EventArgs: { $size: 0 } })).toBe(true)
    expect(matches(body, { Missing: { $exists: false } })).toBe(true)
    expect(matches(body, { EventId: { $exists: true } })).toBe(true)
    expect(matches(body, { Severity: { $ne: 'Info' } })).toBe(true)
    expect(matches({ Modules: [{ Name: 'a' }, { Name: 'b' }] }, { Modules: { $contains: { Name: 'b' } } })).toBe(true)
  })
})

describe('entryMatches', () => {
  const e = entry('SystemStatusNotification', { PreviousState: 'Idle', CurrentState: 'E-Stop' })

  it('filters on name, way, topic, since and validity', () => {
    expect(entryMatches(e, { name: 'SystemStatusNotification' })).toBe(true)
    expect(entryMatches(e, { name: ['SystemStatusResponse', 'SystemStatusNotification'] })).toBe(true)
    expect(entryMatches(e, { way: 'tx' })).toBe(false)
    expect(entryMatches(e, { topic: '/is/iw/tx' })).toBe(true)
    expect(entryMatches(e, { since: 10 })).toBe(false)
    expect(entryMatches(e, { since: 9 })).toBe(true)
    expect(entryMatches(e, { valid: false })).toBe(false)
    expect(entryMatches(e, { match: { CurrentState: 'E-Stop' } })).toBe(true)
    expect(entryMatches(e, { exclude: [10] })).toBe(false)
    expect(entryMatches(e, { exclude: [9, 11] })).toBe(true)
  })

  it('never matches a body filter on a non-ICD payload', () => {
    expect(entryMatches({ ...e, name: undefined, payload: 'garbage' }, { match: {} })).toBe(false)
  })
})

describe('checkFilter', () => {
  it('rejects unknown fields and bad values', () => {
    expect(checkFilter({ name: 'X' })).toBeUndefined()
    expect(checkFilter({ nmae: 'X' })).toMatch(/unknown filter field/)
    expect(checkFilter({ way: 'up' })).toMatch(/way/)
    expect(checkFilter({ match: 3 })).toMatch(/match/)
    expect(checkFilter({ exclude: [1, 2] })).toBeUndefined()
    expect(checkFilter({ exclude: ['1'] })).toMatch(/exclude/)
    expect(checkFilter([])).toMatch(/object/)
  })
})

describe('describeFilter', () => {
  it('counts only the skipped ids inside the window instead of listing them', () => {
    const text = describeFilter({ name: 'SystemStatusNotification', since: 5, exclude: [3, 6, 7] })
    expect(text).toContain('after #5')
    expect(text).toContain('skipping 2 earlier match(es)')
    expect(text).not.toContain('#6')
    expect(describeFilter({ exclude: [1] })).toContain('skipping 1')
    expect(describeFilter({ since: 9, exclude: [1] })).not.toContain('skipping')
  })
})
