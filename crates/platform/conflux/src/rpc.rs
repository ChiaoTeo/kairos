use std::future::Future;
use std::pin::Pin;

use kairos_protocol::control::jsonrpc::RpcResult;

use crate::{ConfluxActor, Context, RpcActorInvocation};

pub struct ConfluxJsonRpcService<A: ConfluxActor> {
    invocation: RpcActorInvocation<A>,
}

impl<A: ConfluxActor> Clone for ConfluxJsonRpcService<A> {
    fn clone(&self) -> Self {
        Self {
            invocation: self.invocation.clone(),
        }
    }
}

impl<A: ConfluxActor> ConfluxJsonRpcService<A> {
    pub fn new(invocation: RpcActorInvocation<A>) -> Self {
        Self { invocation }
    }

    pub async fn call<T, F>(&self, invocation: F) -> RpcResult<T>
    where
        T: Send + 'static,
        F: for<'a> FnOnce(
                &'a mut A,
                &'a mut Context<'a, A>,
            ) -> Pin<Box<dyn Future<Output = RpcResult<T>> + 'a>>
            + Send
            + 'static,
    {
        self.invocation.call(invocation).await
    }
}

#[macro_export]
macro_rules! conflux_json_rpc_actor {
    (
        $vis:vis trait $actor_trait:ident;
        service $service:ident;
        server $server_trait:path;
        methods {
            $(
                $method:ident [$rpc_name:literal] ( $( $param:ident : $param_ty:ty ),* $(,)? )
                    -> $response_ty:ty
                    => $actor_method:ident ( $params_expr:expr );
            )*
        }
    ) => {
        $vis trait $actor_trait: $crate::ConfluxActor {
            $(
                fn $actor_method<'rpc>(
                    &'rpc mut self,
                    params: $crate::conflux_json_rpc_actor!(@params_ty $( $param_ty ),*),
                    context: &'rpc mut $crate::Context<'rpc, Self>,
                ) -> impl ::std::future::Future<
                    Output = ::kairos_protocol::control::jsonrpc::RpcResult<$response_ty>
                > + 'rpc;
            )*
        }

        #[derive(Clone)]
        $vis struct $service<A: $crate::ConfluxActor> {
            inner: $crate::ConfluxJsonRpcService<A>,
        }

        impl<A: $crate::ConfluxActor> $service<A> {
            pub fn new(invocation: $crate::RpcActorInvocation<A>) -> Self {
                Self {
                    inner: $crate::ConfluxJsonRpcService::new(invocation),
                }
            }
        }

        #[::kairos_protocol::control::jsonrpc::async_trait]
        impl<A> $server_trait for $service<A>
        where
            A: $actor_trait,
        {
            $(
                async fn $method(&self, $( $param: $param_ty ),*) -> ::kairos_protocol::control::jsonrpc::RpcResult<$response_ty> {
                    self.inner.call(move |actor, context| {
                        ::std::boxed::Box::pin(async move {
                            actor
                                .$actor_method($params_expr, context)
                                .await
                        })
                    })
                    .await
                }
            )*
        }
    };
    (@params_ty) => { () };
    (@params_ty $param_ty:ty) => { $param_ty };
    (@params_ty $($param_ty:ty),+) => { ( $($param_ty),+ ) };
}
