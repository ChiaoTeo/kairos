#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum ProcessPhase {
    Created,
    Starting,
    Running,
    Stopping,
    Stopped,
    Forced,
    Failed,
}

impl ProcessPhase {
    pub(crate) const fn from_u8(value: u8) -> Self {
        match value {
            0 => Self::Created,
            1 => Self::Starting,
            2 => Self::Running,
            3 => Self::Stopping,
            4 => Self::Stopped,
            5 => Self::Forced,
            _ => Self::Failed,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShutdownMode {
    Drain,
    Immediate,
}
