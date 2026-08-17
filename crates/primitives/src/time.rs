use std::fmt;

use serde::{Deserialize, Serialize};

macro_rules! unit_u64_type {
    ($name:ident) => {
        #[derive(
            Clone,
            Copy,
            Debug,
            Default,
            Eq,
            Ord,
            PartialEq,
            PartialOrd,
            Hash,
            Serialize,
            Deserialize,
        )]
        #[serde(transparent)]
        pub struct $name(u64);

        impl $name {
            pub const fn new(value: u64) -> Self {
                Self(value)
            }
            pub const fn get(self) -> u64 {
                self.0
            }
            pub const fn checked_add(self, other: Self) -> Option<Self> {
                match self.0.checked_add(other.0) {
                    Some(value) => Some(Self(value)),
                    None => None,
                }
            }
            pub const fn saturating_sub(self, other: Self) -> Self {
                Self(self.0.saturating_sub(other.0))
            }
            pub const fn is_multiple_of(self, other: u64) -> bool {
                self.0.is_multiple_of(other)
            }
        }

        impl From<u64> for $name {
            fn from(value: u64) -> Self {
                Self::new(value)
            }
        }

        impl From<$name> for u64 {
            fn from(value: $name) -> Self {
                value.get()
            }
        }

        impl std::ops::Add<u64> for $name {
            type Output = Self;

            fn add(self, rhs: u64) -> Self::Output {
                Self(self.0 + rhs)
            }
        }

        impl std::ops::AddAssign<u64> for $name {
            fn add_assign(&mut self, rhs: u64) {
                self.0 += rhs;
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                self.0.fmt(f)
            }
        }
    };
}

unit_u64_type!(Sequence);
unit_u64_type!(UnixNanos);
unit_u64_type!(Generation);
unit_u64_type!(DurationNanos);
unit_u64_type!(BasisPoints);
