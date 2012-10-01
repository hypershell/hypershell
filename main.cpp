
#include <stdio.h>

#include "hysh/wrapper/Object.hpp"

extern "C" {
#include "hysh/interface/object.h"
#include "hysh/interface/string.h"
#include "hysh/impl/basic_error.h"
#include "hysh/interface/stream_handler.h"
#include "hysh/interface/http.h"

hy_error* test_function() {
    return hy_fatal_error.interface;
}

int main() {
    hy_error* err;
    
    hy_error* error = test_function();
    
    hy_error_code error_code;
    const char *message;
    
    err = error.methods->error_code(error.self, &error_code);
    err = error.methods->error_message(error.self, &message);
    
    int code = error_code & 0xFFF;
    printf("%d: %s", code, message);
    
    return 0;
};


}
