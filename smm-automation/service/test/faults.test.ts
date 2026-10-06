import { describe, expect, it } from 'vitest'
import { DEFAULT_WRONG_TOPIC, FaultError, FaultInjector, INVALID_FIELD, validateFaults, type MockFault } from '../src/mock/faults'

const TX = '/is/iw/tx'
const bodies = (inj: FaultInjector, name: string, body: unknown) =>
  inj.deliveries(TX, name, body).map((d) => ({ ...d, body: JSON.parse(d.payload)[name] }))

describe('mock fault injection', () => {
  it('leaves messages alone without a matching rule', () => {
    const inj = new FaultInjector([{ message: 'ShutdownResponse', action: 'drop' }])
    expect(bodies(inj, 'InitializationResponse', { Status: 'OK' })).toEqual([{ topic: TX, payload: '{"Version":7,"InitializationResponse":{"Status":"OK"}}', delayMs: 0, body: { Status: 'OK' } }])
  })

  it('drops, duplicates, delays, holds and re-routes', () => {
    const one = (f: MockFault) => new FaultInjector([f]).deliveries(TX, 'X', { A: 1 })
    expect(one({ message: 'X', action: 'drop' })).toEqual([])
    expect(one({ message: 'X', action: 'duplicate' })).toHaveLength(2)
    expect(one({ message: 'X', action: 'delay', ms: 700 })[0].delayMs).toBe(700)
    expect(one({ message: 'X', action: 'reorder' })[0]).toMatchObject({ hold: true, delayMs: 2000 })
    expect(one({ message: 'X', action: 'wrongTopic' })[0].topic).toBe(DEFAULT_WRONG_TOPIC)
    expect(one({ message: '*', action: 'wrongTopic', topic: '/is/iw/rx' })[0].topic).toBe('/is/iw/rx')
  })

  it('changes, removes and invalidates fields without touching the original', () => {
    const original = { Status: 'OK', Nested: { Value: 1 } }
    const set = new FaultInjector([{ message: 'X', action: 'set', fields: { Status: 'Error', 'Nested.Value': 2 } }])
    expect(bodies(set, 'X', original)[0].body).toEqual({ Status: 'Error', Nested: { Value: 2 } })
    const unset = new FaultInjector([{ message: 'X', action: 'unset', remove: ['Status', 'Nested.Value'] }])
    expect(bodies(unset, 'X', original)[0].body).toEqual({ Nested: {} })
    const invalid = new FaultInjector([{ message: 'X', action: 'invalid' }])
    expect(bodies(invalid, 'X', original)[0].body).toEqual({ ...original, [INVALID_FIELD]: true })
    expect(original).toEqual({ Status: 'OK', Nested: { Value: 1 } })
  })

  it('matches on body values and honours skip and count', () => {
    const inj = new FaultInjector([{ message: 'SystemStatusNotification', when: { CurrentState: 'Idle' }, action: 'drop', skip: 1, count: 1 }])
    const idle = { PreviousState: 'Initializing', CurrentState: 'Idle' }
    expect(inj.deliveries(TX, 'SystemStatusNotification', { PreviousState: 'PowerOn', CurrentState: 'NotInitialized' })).toHaveLength(1)
    expect(inj.deliveries(TX, 'SystemStatusNotification', idle)).toHaveLength(1)
    expect(inj.deliveries(TX, 'SystemStatusNotification', idle)).toHaveLength(0)
    expect(inj.deliveries(TX, 'SystemStatusNotification', idle)).toHaveLength(1)
    expect(inj.stats()[0]).toMatchObject({ matched: 3, applied: 1 })
  })

  it('applies only the first matching rule', () => {
    const inj = new FaultInjector([
      { message: 'X', action: 'duplicate' },
      { message: '*', action: 'drop' },
    ])
    expect(inj.deliveries(TX, 'X', {})).toHaveLength(2)
    expect(inj.deliveries(TX, 'Y', {})).toHaveLength(0)
  })

  it('rejects malformed rules', () => {
    expect(validateFaults(undefined)).toEqual([])
    const bad: unknown[] = [
      {},
      [{ action: 'drop' }],
      [{ message: 'X', action: 'explode' }],
      [{ message: 'X', action: 'delay' }],
      [{ message: 'X', action: 'set' }],
      [{ message: 'X', action: 'unset', remove: [] }],
      [{ message: 'X', action: 'drop', count: -1 }],
      [{ message: 'X', action: 'drop', when: [] }],
      [{ message: 'X', action: 'drop', typo: 1 }],
    ]
    for (const input of bad) expect(() => validateFaults(input), JSON.stringify(input)).toThrow(FaultError)
  })
})
