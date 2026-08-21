use proc_macro::TokenStream;
use quote::{format_ident, quote};
use syn::parse::Parser;
use syn::{
    FnArg, GenericArgument, ItemTrait, LitStr, Meta, Pat, PathArguments, ReturnType, TraitItem,
    TraitItemFn, Type, parse_macro_input,
};

#[proc_macro_attribute]
pub fn conflux_rpc(args: TokenStream, input: TokenStream) -> TokenStream {
    let namespace = parse_namespace(args);
    let input = parse_macro_input!(input as ItemTrait);

    match expand_conflux_rpc(namespace, input) {
        Ok(tokens) => tokens.into(),
        Err(error) => error.to_compile_error().into(),
    }
}

fn parse_namespace(args: TokenStream) -> Option<LitStr> {
    let parser = syn::punctuated::Punctuated::<Meta, syn::Token![,]>::parse_terminated;
    parser.parse(args).ok().and_then(|items| {
        items.into_iter().find_map(|meta| match meta {
            Meta::NameValue(value) if value.path.is_ident("namespace") => match value.value {
                syn::Expr::Lit(expr) => match expr.lit {
                    syn::Lit::Str(value) => Some(value),
                    _ => None,
                },
                _ => None,
            },
            _ => None,
        })
    })
}

fn expand_conflux_rpc(
    namespace: Option<LitStr>,
    mut item: ItemTrait,
) -> syn::Result<proc_macro2::TokenStream> {
    let trait_name = item.ident.clone();
    let server_trait = format_ident!("{trait_name}Server");
    let bind_macro = format_ident!("{}_conflux_actor", to_snake_case(&trait_name.to_string()));
    let rpc_attr = match namespace {
        Some(namespace) => quote! {
            #[kairos_protocol::control::jsonrpc::rpc(client, server, namespace = #namespace)]
        },
        None => quote! {
            #[kairos_protocol::control::jsonrpc::rpc(client, server)]
        },
    };

    let mut methods = Vec::new();
    for item in &mut item.items {
        let TraitItem::Fn(method) = item else {
            continue;
        };
        ensure_async(method)?;
        let method_name = method.sig.ident.clone();
        let rpc_name = method_name.to_string();
        if !has_method_attr(method) {
            method
                .attrs
                .push(syn::parse_quote!(#[method(name = #rpc_name)]));
        }
        let response = rpc_result_response(method)?;
        let params = method_params(method)?;
        let param_idents = params.iter().map(|param| &param.ident).collect::<Vec<_>>();
        let param_types = params.iter().map(|param| &param.ty).collect::<Vec<_>>();
        let request_params = match param_idents.as_slice() {
            [] => quote! { () },
            [one] => quote! { #one },
            many => quote! { ( #(#many),* ) },
        };
        methods.push(quote! {
            #method_name[#rpc_name](#(#param_idents: #param_types),*)
                -> #response
                => #method_name(#request_params);
        });
    }

    Ok(quote! {
        #rpc_attr
        #item

        #[macro_export]
        macro_rules! #bind_macro {
            ($vis:vis trait $actor_trait:ident; service $service:ident;) => {
                kairos_conflux::conflux_json_rpc_actor! {
                    $vis trait $actor_trait;
                    service $service;
                    server $crate::#server_trait;
                    methods {
                        #(#methods)*
                    }
                }
            };
        }
    })
}

struct Param {
    ident: syn::Ident,
    ty: Type,
}

fn ensure_async(method: &TraitItemFn) -> syn::Result<()> {
    if method.sig.asyncness.is_some() {
        Ok(())
    } else {
        Err(syn::Error::new_spanned(
            &method.sig.ident,
            "conflux_rpc methods must be async",
        ))
    }
}

fn has_method_attr(method: &TraitItemFn) -> bool {
    method
        .attrs
        .iter()
        .any(|attr| attr.path().is_ident("method"))
}

fn rpc_result_response(method: &TraitItemFn) -> syn::Result<Type> {
    let ReturnType::Type(_, output) = &method.sig.output else {
        return Err(syn::Error::new_spanned(
            &method.sig.ident,
            "conflux_rpc methods must return RpcResult<T>",
        ));
    };
    let Type::Path(path) = output.as_ref() else {
        return Err(syn::Error::new_spanned(
            output,
            "conflux_rpc methods must return RpcResult<T>",
        ));
    };
    let segment = path.path.segments.last().ok_or_else(|| {
        syn::Error::new_spanned(output, "conflux_rpc methods must return RpcResult<T>")
    })?;
    if segment.ident != "RpcResult" {
        return Err(syn::Error::new_spanned(
            output,
            "conflux_rpc methods must return RpcResult<T>",
        ));
    }
    let PathArguments::AngleBracketed(arguments) = &segment.arguments else {
        return Err(syn::Error::new_spanned(
            output,
            "conflux_rpc methods must return RpcResult<T>",
        ));
    };
    let Some(GenericArgument::Type(response)) = arguments.args.first() else {
        return Err(syn::Error::new_spanned(
            output,
            "conflux_rpc methods must return RpcResult<T>",
        ));
    };
    Ok(response.clone())
}

fn method_params(method: &TraitItemFn) -> syn::Result<Vec<Param>> {
    method
        .sig
        .inputs
        .iter()
        .filter_map(|input| match input {
            FnArg::Receiver(_) => None,
            FnArg::Typed(param) => Some(param),
        })
        .map(|param| {
            let Pat::Ident(ident) = param.pat.as_ref() else {
                return Err(syn::Error::new_spanned(
                    &param.pat,
                    "conflux_rpc parameters must use simple identifiers",
                ));
            };
            Ok(Param {
                ident: ident.ident.clone(),
                ty: (*param.ty).clone(),
            })
        })
        .collect()
}

fn to_snake_case(value: &str) -> String {
    let mut output = String::new();
    for (index, ch) in value.chars().enumerate() {
        if ch.is_uppercase() {
            if index > 0 {
                output.push('_');
            }
            output.extend(ch.to_lowercase());
        } else {
            output.push(ch);
        }
    }
    output
}
