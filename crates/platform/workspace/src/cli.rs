//! Shared output policy for command-line boundaries.
//!
//! Workspace owns the configured default; command-line binaries own when to
//! render. Keeping the format and generic rendering here prevents each
//! canonical binary from inventing a different text/table representation.

use std::fmt;
use std::str::FromStr;

use serde_json::Value;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum OutputFormat {
    #[default]
    Json,
    Text,
    Table,
}

impl FromStr for OutputFormat {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "json" => Ok(Self::Json),
            "text" => Ok(Self::Text),
            "table" => Ok(Self::Table),
            other => Err(format!(
                "unsupported output format: {other}; expected json, text, or table"
            )),
        }
    }
}

impl fmt::Display for OutputFormat {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Json => "json",
            Self::Text => "text",
            Self::Table => "table",
        })
    }
}

impl OutputFormat {
    pub fn from_workspace(value: &str) -> Result<Self, String> {
        value.parse()
    }
}

pub fn render(value: &Value, format: OutputFormat) -> String {
    match format {
        OutputFormat::Json => serde_json::to_string_pretty(value).expect("JSON serialization"),
        OutputFormat::Text => {
            let mut output = String::new();
            render_text(value, "", &mut output);
            output.trim_end_matches('\n').to_owned()
        },
        OutputFormat::Table => render_table(value),
    }
}

/// Render caller-selected columns without imposing business field semantics.
pub fn render_compact_table(headers: &[&str], rows: &[Vec<String>]) -> String {
    if headers.is_empty() {
        return String::new();
    }
    let widths = (0..headers.len())
        .map(|index| {
            std::iter::once(headers[index].chars().count())
                .chain(
                    rows.iter()
                        .map(|row| row.get(index).map_or(0, |value| value.chars().count())),
                )
                .max()
                .unwrap_or_default()
        })
        .collect::<Vec<_>>();
    let line = |cells: &[String]| {
        (0..headers.len())
            .map(|index| {
                let cell = cells.get(index).map(String::as_str).unwrap_or_default();
                if index + 1 == headers.len() {
                    cell.to_owned()
                } else {
                    format!("{cell:<width$}", width = widths[index])
                }
            })
            .collect::<Vec<_>>()
            .join("  ")
    };
    let header = headers
        .iter()
        .map(|value| (*value).to_owned())
        .collect::<Vec<_>>();
    let separator = widths
        .iter()
        .map(|width| "─".repeat(*width))
        .collect::<Vec<_>>();
    std::iter::once(line(&header))
        .chain(std::iter::once(line(&separator)))
        .chain(rows.iter().map(|row| line(row)))
        .collect::<Vec<_>>()
        .join("\n")
}

fn render_text(value: &Value, prefix: &str, output: &mut String) {
    match value {
        Value::Object(values) => {
            for (key, value) in values {
                let name = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                render_text(value, &name, output);
            }
        },
        Value::Array(values) => {
            for (index, value) in values.iter().enumerate() {
                render_text(value, &format!("{prefix}[{index}]"), output);
            }
        },
        _ => output.push_str(&format!("{prefix}: {value}\n")),
    }
}

fn render_table(value: &Value) -> String {
    match value {
        Value::Object(values) => {
            let rows = values
                .iter()
                .map(|(key, value)| vec![key.clone(), cell(value)])
                .collect::<Vec<_>>();
            ascii_table(&["key", "value"], &rows)
        },
        Value::Array(values) if values.is_empty() => String::new(),
        Value::Array(values) if values.iter().all(Value::is_object) => {
            let mut keys = values
                .iter()
                .flat_map(|item| {
                    item.as_object()
                        .into_iter()
                        .flat_map(|object| object.keys())
                })
                .cloned()
                .collect::<Vec<_>>();
            keys.sort();
            keys.dedup();
            let rows = values
                .iter()
                .map(|item| {
                    let object = item.as_object().expect("object array checked");
                    keys.iter()
                        .map(|key| object.get(key).map_or_else(String::new, cell))
                        .collect::<Vec<_>>()
                })
                .collect::<Vec<_>>();
            let headers = keys.iter().map(String::as_str).collect::<Vec<_>>();
            ascii_table(&headers, &rows)
        },
        Value::Array(values) => ascii_table(
            &["value"],
            &values
                .iter()
                .map(|value| vec![cell(value)])
                .collect::<Vec<_>>(),
        ),
        _ => ascii_table(&["value"], &[vec![cell(value)]]),
    }
}

fn cell(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => serde_json::to_string(value).expect("JSON serialization"),
    }
}

fn ascii_table(headers: &[&str], rows: &[Vec<String>]) -> String {
    let widths = (0..headers.len())
        .map(|index| {
            std::iter::once(headers[index].len())
                .chain(rows.iter().map(|row| row.get(index).map_or(0, String::len)))
                .max()
                .unwrap_or(0)
        })
        .collect::<Vec<_>>();
    let border = format!(
        "+{}+",
        widths
            .iter()
            .map(|width| "-".repeat(width + 2))
            .collect::<Vec<_>>()
            .join("+")
    );
    let line = |row: &[String]| {
        format!(
            "| {} |",
            row.iter()
                .enumerate()
                .map(|(index, value)| format!("{value:<width$}", width = widths[index]))
                .collect::<Vec<_>>()
                .join(" | ")
        )
    };
    let mut output = vec![
        border.clone(),
        line(
            &headers
                .iter()
                .map(|value| (*value).to_owned())
                .collect::<Vec<_>>(),
        ),
        border,
    ];
    output.extend(rows.iter().map(|row| line(row)));
    output.push(output[0].clone());
    output.join("\n")
}

#[cfg(test)]
mod tests {
    use std::str::FromStr;

    use serde_json::json;

    use super::{OutputFormat, render, render_compact_table};

    #[test]
    fn parses_all_supported_formats() {
        assert_eq!(
            OutputFormat::from_str("table").unwrap(),
            OutputFormat::Table
        );
        assert!(OutputFormat::from_str("yaml").is_err());
    }

    #[test]
    fn renders_object_table() {
        let output = render(&json!({"status": "ready"}), OutputFormat::Table);
        assert!(output.contains("status"));
        assert!(output.contains("ready"));
        assert!(output.starts_with('+'));
    }

    #[test]
    fn renders_compact_caller_selected_columns() {
        let output = render_compact_table(
            &["ACCOUNT", "MODE"],
            &[vec!["paper-account".into(), "paper".into()]],
        );

        assert_eq!(
            output,
            "ACCOUNT        MODE\n─────────────  ─────\npaper-account  paper"
        );
    }
}
