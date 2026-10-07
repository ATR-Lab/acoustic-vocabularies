// Exact integer helpers used to generate the renderer tables (spec D2, D4) without
// floating-point sin, cos or pow.
//
// - `Q124`: unsigned fixed point with 124 fraction bits in a `UInt128`. Products use
//   the full 256-bit `multipliedFullWidth`, so no precision is lost before the shift.
// - `WideUInt`: a minimal arbitrary-width unsigned integer (multiply, shift, compare),
//   used to prove each phase increment is the correctly rounded value.
// - `isqrt`: floor of the exact square root of a `UInt128` (spec D6).

/// Unsigned fixed point, value = raw / 2^124. Inputs stay below 2^126 (< 4.0).
enum Q124 {
    static let fractionBits = 124
    static let one: UInt128 = 1 << 124

    /// floor(a * b / 2^124). Requires a, b < 2^126, so the result is < 2^128.
    @inline(__always)
    static func mul(_ a: UInt128, _ b: UInt128) -> UInt128 {
        let (high, low) = a.multipliedFullWidth(by: b)
        precondition(high >> 124 == 0, "Q124 product out of range")
        return (high << 4) | (low >> 124)
    }

    /// atan(1/x) in Q(bits) by its Taylor series, floor-truncated per term.
    private static func atanInverse(_ x: UInt128, bits: Int) -> UInt128 {
        let x2 = x * x
        var term = (UInt128(1) << bits) / x
        var positive = term
        var negative: UInt128 = 0
        var k: UInt128 = 1
        while term != 0 {
            term /= x2
            let contribution = term / (2 * k + 1)
            if k & 1 == 1 { negative += contribution } else { positive += contribution }
            k += 1
        }
        return positive - negative
    }

    /// pi in Q124 from Machin's formula, computed in Q126 (2 guard bits; the series
    /// truncation error is a few hundred Q126 units at most) and truncated.
    static let pi: UInt128 = {
        let piQ126 = 16 * atanInverse(5, bits: 126) - 4 * atanInverse(239, bits: 126)
        return piQ126 >> 2
    }()

    /// ln 2 in Q124 from ln 2 = 2 * atanh(1/3) = sum 2 / ((2k+1) 3^(2k+1)), in Q126.
    static let ln2: UInt128 = {
        let bits = 126
        var power = (UInt128(1) << bits) / 3  // 3^-(2k+1)
        var total: UInt128 = 0
        var k: UInt128 = 0
        while power != 0 {
            total += power / (2 * k + 1)
            power /= 9
            k += 1
        }
        return (2 * total) >> 2
    }()

    /// sin(pi/2 * num/den) in Q124 for 0 <= num <= den, by its Taylor series.
    /// Absolute error is below 2^10 units of Q124 (pi error, per-term truncation).
    static func sinQuarter(_ num: Int, _ den: Int) -> UInt128 {
        precondition(den > 0 && num >= 0 && num <= den)
        let d = UInt128(2 * den)
        let n = UInt128(num)
        // x = pi * num / (2 den), floor, without overflowing 128 bits.
        let x = (pi / d) * n + ((pi % d) * n) / d
        let x2 = mul(x, x)
        var term = x
        var positive = x
        var negative: UInt128 = 0
        var k: UInt128 = 1
        while term != 0 {
            term = mul(term, x2) / ((2 * k) * (2 * k + 1))
            if k & 1 == 1 { negative += term } else { positive += term }
            k += 1
        }
        if negative >= positive { return 0 }
        return min(positive - negative, one)
    }

    /// exp(y) in Q124 for 0 <= y < 0.7 (raw y < 0.7 * 2^124), by its Taylor series.
    static func exp(_ y: UInt128) -> UInt128 {
        var term = one
        var total = one
        var k: UInt128 = 1
        while term != 0 {
            term = mul(term, y) / k
            total += term
            k += 1
        }
        return total
    }

    /// 2^(p/12) in Q124 for -12 < p < 12.
    static func pow2Twelfth(_ p: Int) -> UInt128 {
        precondition(p > -12 && p < 12)
        if p >= 0 {
            return exp(ln2 * UInt128(p) / 12)
        }
        return exp(ln2 * UInt128(p + 12) / 12) >> 1
    }

    /// Round half up of v / 2^shift, plus whether the discarded fraction is within
    /// `margin` of the halfway point (then the rounding is not proven by this method).
    static func roundShift(_ v: UInt128, _ shift: Int, margin: UInt128) -> (value: UInt128, nearTie: Bool) {
        let half = UInt128(1) << (shift - 1)
        let fraction = v & ((UInt128(1) << shift) - 1)
        let distance = fraction >= half ? fraction - half : half - fraction
        return ((v + half) >> shift, distance <= margin)
    }
}

/// Floor of the exact square root of `n`.
func isqrt(_ n: UInt128) -> UInt128 {
    if n < 2 { return n }
    let bits = UInt128.bitWidth - n.leadingZeroBitCount
    var x = UInt128(1) << ((bits + 1) / 2)  // >= sqrt(n)
    while true {
        let y = (x + n / x) >> 1
        if y >= x { return x }
        x = y
    }
}

/// Minimal arbitrary-precision unsigned integer: little-endian 64-bit limbs.
struct WideUInt: Comparable, Sendable {
    private(set) var limbs: [UInt64]

    init(_ value: UInt64) {
        limbs = value == 0 ? [] : [value]
    }

    private init(limbs: [UInt64]) {
        var trimmed = limbs
        while let last = trimmed.last, last == 0 { trimmed.removeLast() }
        self.limbs = trimmed
    }

    static func * (a: WideUInt, b: WideUInt) -> WideUInt {
        if a.limbs.isEmpty || b.limbs.isEmpty { return WideUInt(0) }
        var out = [UInt64](repeating: 0, count: a.limbs.count + b.limbs.count)
        for i in a.limbs.indices {
            var carry: UInt64 = 0
            for j in b.limbs.indices {
                let (high, low) = a.limbs[i].multipliedFullWidth(by: b.limbs[j])
                let (s1, c1) = low.addingReportingOverflow(out[i + j])
                let (s2, c2) = s1.addingReportingOverflow(carry)
                out[i + j] = s2
                carry = high &+ (c1 ? 1 : 0) &+ (c2 ? 1 : 0)  // cannot overflow
            }
            out[i + b.limbs.count] = carry
        }
        return WideUInt(limbs: out)
    }

    func shiftedLeft(_ bits: Int) -> WideUInt {
        precondition(bits >= 0)
        if limbs.isEmpty { return self }
        let words = bits / 64
        let shift = UInt64(bits % 64)
        var out = [UInt64](repeating: 0, count: limbs.count + words + 1)
        for (i, limb) in limbs.enumerated() {
            out[i + words] |= limb << shift
            if shift != 0 { out[i + words + 1] |= limb >> (64 - shift) }
        }
        return WideUInt(limbs: out)
    }

    func power(_ exponent: Int) -> WideUInt {
        precondition(exponent >= 0)
        var result = WideUInt(1)
        for _ in 0..<exponent { result = result * self }
        return result
    }

    static func < (a: WideUInt, b: WideUInt) -> Bool {
        if a.limbs.count != b.limbs.count { return a.limbs.count < b.limbs.count }
        for i in a.limbs.indices.reversed() where a.limbs[i] != b.limbs[i] {
            return a.limbs[i] < b.limbs[i]
        }
        return false
    }
}
