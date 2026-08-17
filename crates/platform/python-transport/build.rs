fn main() {
    // CPython extension symbols are resolved by the loading interpreter on
    // macOS. Cargo builds do not otherwise inherit setuptools-rust's linker
    // mode, so keep direct crate builds equivalent to the wheel build.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-link-arg=-undefined");
        println!("cargo:rustc-link-arg=dynamic_lookup");
    }
}
