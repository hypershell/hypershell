
#include "hysh/impl/basic_error.h"

//hy_define_basic_error(hy_query_interface_error, hy_query_interface_error_code, "Cannot cast to requested object");

hy_basic_error hy_query_interface_error = {
    {
        &hy_query_interface_error,
        &hy_basic_error_methods,
    },
    hy_query_interface_error_code,
    "Cannot cast to requested object"
};

hy_error* hy_basic_error_add_ref(void *self) { 
    return 0;
}

hy_error* hy_basic_error_de_ref(void *self) {
    return 0;
}

hy_error* hy_basic_error_class_id(void *self, hy_cid *retval) {
    *retval = hy_basic_error_cid;
    return 0;
}

hy_error* hy_basic_error_query_interface(void *_self, hy_iid iid, hy_object **retval) {
    hy_basic_error *self = (hy_basic_error*) _self;
    
    if(iid == hy_error_iid) {
        *retval = (hy_object*) &self->interface;
        return 0;
    } else {
        *retval = 0;
        return &hy_query_interface_error.interface;
    }
}

hy_error* hy_basic_error_error_code(void *_self, uint64_t *retval) {
    hy_basic_error *self = (hy_basic_error*) _self;
    
    *retval = self->error_code;
    return 0;
}

hy_error* hy_basic_error_error_message(void *_self, const char **retval) {
    hy_basic_error *self = (hy_basic_error*) _self;
    
    *retval = self->error_message;
    return 0;
}