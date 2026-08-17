#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum ProcessPhase {
    Created,
    Starting,
    Running,
    Quiescing,
    DrainingInputs,
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
            3 => Self::Quiescing,
            4 => Self::DrainingInputs,
            5 => Self::Stopping,
            6 => Self::Stopped,
            7 => Self::Forced,
            _ => Self::Failed,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShutdownMode {
    Drain,
    Immediate,
}
