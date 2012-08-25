
#pragma once

#include "hysh/interface/table.h"
#include "hysh/interface/stream.h"

struct hy_stream_handler_methods;
struct hy_stream_handler_listener_methods;
struct hy_stream_result_writer_methods;

typedef struct hy_stream_handler {
    void *self;
    
    struct hy_stream_handler_methods *methods;
} hy_stream_handler;

typedef struct hy_stream_handler_listener {
    void *self;
    
    struct hy_stream_handler_listener_methods *methods;
} hy_stream_handler_listener;

typedef struct hy_stream_result_writer {
    void *self;
    
    struct hy_stream_result_writer_methods *methods;
} hy_stream_result_writer;

typedef struct hy_stream_handler_methods {
    hy_object_methods parent;
    
    hy_error (*process_stream)(void *self, 
        hy_table args,
        hy_read_stream input_stream, 
        hy_stream_result_writer writer);
    
} hy_stream_handler_methods;

typedef struct hy_stream_handler_listener_methods {
    hy_object_methods parent;
    
    hy_error (*on_result_stream_available)(void *self,
        hy_read_stream result_stream);
        
    hy_error (*on_error)(void *self,
        hy_error error);
    
} hy_stream_handler_listener_methods;

typedef struct hy_stream_result_writer_methods {
    hy_object_methods parent;
    
    hy_error (*write_result)(void *self, hy_read_stream result_stream);
    
    hy_error (*write_error)(void *self, hy_error error);
    
} hy_stream_result_writer_methods;