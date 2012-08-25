
#pragma once

#include "hysh/interface/stream.h"

struct hy_http_request_line_methods;
struct hy_http_response_line_methods;
struct hy_http_handler_methods;
struct hy_http_handler_listener_methods;
struct hy_http_response_writer_methods;

typedef struct hy_http_request_line {
    void *self;
    
    struct hy_http_request_line_methods *methods;
} hy_http_request_line;

typedef struct hy_http_response_line {
    void *self;
    
    struct hy_http_response_line_methods *methods;
} hy_http_response_line;

typedef struct hy_http_handler {
    void *self;
    
    struct hy_http_handler_methods *methods;

} hy_http_handler;

typedef struct hy_http_handler_listener {
    void *self;
    
    struct hy_http_handler_listener_methods *methods;
} hy_http_handler_listener;

typedef struct hy_http_response_writer {
    void *self;
    
    struct hy_http_response_writer_methods *methods;
} hy_http_response_writer;

typedef struct hy_http_request_line_methods {
    hy_object_methods parent;
    
    hy_error (*http_version)(void *self, hy_string *retval);
    
    hy_error (*request_method)(void *self, hy_string *retval);
    
    hy_error (*request_path)(void *self, hy_string *retval);
    
    hy_error (*query_string)(void *self, hy_string *retval);
    
} hy_http_request_line_methods;

typedef struct hy_http_response_line_methods {
    hy_object_methods parent;
    
    hy_error (*http_version)(void *self, hy_string *retval);
    
    hy_error (*status_code)(void *self, uint32_t *retval);
    
    hy_error (*status_message)(void *self, hy_string *retval);
    
} hy_http_response_line_methods;

typedef struct hy_http_handler_methods {
    hy_object_methods parent;
    
    hy_error (*handle_http_request)(void *self, 
        hy_http_request_line request_line,
        hy_table request_headers,
        hy_read_stream request_body,
        hy_http_response_writer response_writer);
    
} hy_http_handler_methods;

typedef struct hy_http_handler_listener_methods {
    hy_object_methods parent;
    
    hy_error (*on_http_response_available)(void *self,
        hy_http_response_line response_line,
        hy_table request_headers,
        hy_read_stream response_stream);
    
} hy_http_handler_listener_methods;

typedef struct hy_http_response_writer_methods {
    hy_object_methods parent;
    
    hy_error (*write_response)(void *self,
        hy_http_response_line response_line,
        hy_table request_headers,
        hy_read_stream response_stream);
    
} hy_http_handler_listener_methods;