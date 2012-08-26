
#include "hysh/impl/basic_error.h"

hy_error hy_basic_error_add_ref(void *self) { 
    return hy_success;
}

hy_error hy_basic_error_de_ref(void *self) {
    return hy_success;
}

hy_error hy_basic_error_query_interface(void *_self, hy_iid iid, hy_object *retval) {
    hy_basic_error *self = (hy_basic_error*) _self;
    
    if(iid == hy_error_iid) {
        hy_cast(*retval, hy_object, self->interface);
        return hy_success;
    } else {
        *retval = hy_null;
        return hy_fatal_error.interface;
    }
}

hy_error hy_basic_error_error_code(void *_self, uint64_t *retval) {
    hy_basic_error *self = (hy_basic_error*) _self;
    
    *retval = self->error_code;
    return hy_success;
}

hy_error hy_basic_error_error_message(void *_self, const char **retval) {
    hy_basic_error *self = (hy_basic_error*) _self;
    
    *retval = self->error_message;
    return hy_success;
}

hy_error_methods hy_basic_error_methods = {
    {
        hy_basic_error_add_ref,
        hy_basic_error_de_ref,
        hy_basic_error_query_interface
    },
    hy_basic_error_error_code,
    hy_basic_error_error_message
};

hy_define_basic_error(hy_fatal_error, hy_fatal_error_code, "A fatal error has occured");
hy_define_basic_error(hy_failure, hy_failure_code, "Fail to perform operation");
hy_define_basic_error(hy_query_interface_error, hy_query_interface_error_code, "Cannot cast to requested object");
hy_define_basic_error(hy_not_found, hy_not_found_code, "Requested element not found");
hy_define_basic_error(hy_not_implemented, hy_not_implemented_code, "Method not implemented");